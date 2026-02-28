import pdfplumber
import requests
import json
import re
import os
import warnings
from dotenv import load_dotenv
load_dotenv()

warnings.filterwarnings("ignore", message=".*CropBox missing.*")

def download_pdf(url, local_path):
     # Ensure the directory exists
    os.makedirs(os.path.dirname(os.path.expanduser(local_path)), exist_ok=True)
    response = requests.get(url)
    response.raise_for_status()  # Raises an error for bad status
    with open(local_path, 'wb') as f:
        f.write(response.content)
    print(f"Downloaded File to {local_path}")


#Extract all text from PDFs
def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())


# Clean the questions and answers text
def clean_text(text):
    cleaned_lines = []

    for line in text.splitlines():
        line = line.strip()
        # Remove footer lines
        if "Organizing Institute:" in line:
            continue
        #Pages text
        if re.search(r"Page\s+\d+\s+of\s+\d+", line):
            continue
        # Remove subject header line <subject name> (<subject code>) subject atleast 2 chars in code to avoid removing options. 
        if re.search(r"\([A-Z0-9]{2,5}\)$", line.strip()):
            continue
        # remove GATE year logo text
        if re.search(r"GATE\s*\d{4}", line):
            continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def normalize_text(text):
    text = clean_text(text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)  # collapse blank lines
    return text.strip()

#Parse answers into dictionary
def parse_answers(answer_text):
    answers = {}
    for line in answer_text.splitlines():
        line = line.strip()

        # Match table rows like:
        # 1  MCQ  GA  A
        # 25 MSQ  CS-2 A;B
        # 30 NAT  CS-2 3 to 3

        match = re.match(r"^(\d+)\s+(MCQ|MSQ|NAT)\s+\S+\s+(.+)$", line)

        if match:
            q_number = match.group(1)
            q_type = match.group(2)
            key = match.group(3).strip()

            qid = f"Q{q_number}"

            if q_type == "MSQ":
                answers[qid] = key.split(";")
            elif q_type == "MCQ":
                answers[qid] = key
            else:  # NAT
                answers[qid] = key
    return answers


#Parse questions
def parse_questions(text, answer_dict):
    question_blocks = re.findall(r"(Q\.\d+[\s\S]*?)(?=Q\.\d+|\Z)", text)
    question_blocks = re.findall(r"(Q\.\s*\d+[\s\S]*?)(?=Q\.\s*\d+|\Z)",text)
    questions = []

    for block in question_blocks:
        q_match = re.match(r"Q\.\s*(\d+)\s+(.*)", block.strip(), re.DOTALL)
        if not q_match:
            continue

        q_number = int(q_match.group(1))
        qid = f"Q{q_number}"
        content = q_match.group(2).strip()

        # Extract options
        options = dict(re.findall(r"\((A|B|C|D)\)\s*(.*?)(?=\n\(|$)", content, re.DOTALL))

        # Extract question text
        split_match = re.split(r"\(A\)", content)
        question_text = split_match[0].strip() if split_match else content.strip()
        
        # Skip meta text or garbage
        if question_text in {"–", "", "Carry ONE mark Each", "Carry TWO marks Each"}:
            continue

        # Get the answer (if available)
        answer = answer_dict.get(qid, None)

        # Determine type from answer
        if isinstance(answer, list):
            qtype = "MSQ"
        elif isinstance(answer, str) and re.match(r"^[A-D]$", answer):
            qtype = "MCQ"
        elif answer is not None:
            qtype = "NAT"
        else:
            qtype = "Unknown"
        
        # Determine marks based on question number
        if 1 <= q_number <= 5 or 11 <= q_number <= 35:
            marks = 1
        elif 6 <= q_number <= 10 or 36 <= q_number <= 65:
            marks = 2
        else:
            marks = 1  # default fallback


        questions.append({
            "question_number": q_number,
            "question": question_text,
            "options": options,
            "type": qtype,
            "marks": marks,
            "answer": answer
        })

    return questions


def extractProcess(i):
    # env = i['env']
    
    # output_path = env.get('MLC_GATE_OUTPUT_JSON_PATH')

    # if (output_path and os.path.exists(output_path)) and not env.get('MLC_SKIP_CACHE'):
    #     print(f"++++++++++++++++++++++++++ Using cached dataset JSON at {output_path} ++++++++++++++++++++++++++++++++++++")
    #     with open(output_path, "r", encoding="utf-8") as f:
    #         i['state']['questions'] = json.load(f)
    #     return {'return': 0}

    # print("Skipping cache and processing PDFs to extract questions and answers.")
    # question_pdf = env.get('MLC_GATE_QUESTION_PDF_PATH')
    # answer_pdf = env.get('MLC_GATE_ANSWER_PDF_PATH')
    env = i['env']
    state = i['state']

    exam_name = env.get('EXAM_NAME')
    if not exam_name:
        raise ValueError("EXAM_NAME not set in environment, please set it using --env.EXAM_NAME=<your_exam_name>")
    
    output_dir = os.path.expanduser(
        env.get('MLC_GATE_OUTPUT_DIR', '~/MLC/repos/local/cache/gate-exam-data')
    )

    os.makedirs(output_dir, exist_ok=True)

    # Construct {exam_name}.json
    output_path = os.path.join(output_dir, f"{exam_name}.json")

    skip_cache = env.get('MLC_SKIP_CACHE')

    # Use cache if file exists and skip flag NOT set
    if os.path.exists(output_path) and not skip_cache:
        print(f"++++++++++++++++++++++++++ Using cached dataset JSON at {output_path} ++++++++++++++++++++++++++++++++++++")
        with open(output_path, "r", encoding="utf-8") as f:
            state['questions'] = json.load(f)
        return {'return': 0}

    # Otherwise process PDFs
    if (skip_cache):
        print("Skipping cache. Processing PDFs to extract questions and answers.")

    if(not os.path.exists(output_path)):
        print(f"No cached JSON found at {output_path}. Processing PDFs to extract questions and answers.")

    question_pdf = env.get('MLC_GATE_QUESTION_PDF_PATH')
    answer_pdf = env.get('MLC_GATE_ANSWER_PDF_PATH')

    # URL for downloading PDFs if paths not provided 
    questionpdf_url = env.get(
        'MLC_GATE_QUESTION_PDF_URL',
        "https://github.com/user-attachments/files/20423322/CS25set2-questionPaper.pdf"
    )

    answerpdf_url = env.get(
        'MLC_GATE_ANSWER_PDF_URL',
        "https://github.com/user-attachments/files/20423320/CS25set2-answerKey.pdf"
    )

    # Download ONLY if path not set
    if question_pdf is None:
        question_pdf = os.path.expanduser(
            '~/MLC/repos/local/cache/gate-exam-data/paper.pdf'
        )
        print("Downloading Question Paper PDF ..")
        download_pdf(questionpdf_url, question_pdf)
    else:
        print("Using provided Question Paper path:", question_pdf)

    if answer_pdf is None:
        answer_pdf = os.path.expanduser(
            '~/MLC/repos/local/cache/gate-exam-data/key.pdf'
        )
        print("Downloading Answer Key PDF ..")
        download_pdf(answerpdf_url, answer_pdf)
    else:
        print("Using provided Answer Key path:", answer_pdf)
    # print("DEBUG: Skipping downloading PDFs")

    # Extract and clean text
    print("Extracting text from PDFs...")
    qtext = extract_text(question_pdf)
    cleaned_qtext = normalize_text(qtext)
    atext = extract_text(answer_pdf)
    cleaned_atext = normalize_text(atext)


    print("----- RAW ANSWER TEXT SAMPLE -----")
    print(cleaned_atext[:2000])
    print("-----------------------------------")
    
    # Parse answers and questions
    answer_key = parse_answers(cleaned_atext)
    questions = parse_questions(cleaned_qtext, answer_key)
    
    # Store in state
    i['state']['questions'] = questions
    i['state']['answers'] = answer_key
    
    return {'return': 0}


def outputProcess(i):
    env = i['env']
    state = i['state']
    
    questions = state['questions']
    exam_name = env.get('EXAM_NAME', 'exam')
    base_dir = os.path.expanduser('~/MLC/repos/local/cache/gate-exam-data/')
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(base_dir), exist_ok=True)
    
    # Save to JSON
    filename = f"{exam_name}.json"
    output_path = os.path.join(base_dir, filename)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)

    env['MLC_GATE_OUTPUT_JSON_PATH'] = output_path
    print("*"*100)
    print(f"Using {output_path} with {len(questions)} questions.")
    print("*"*100)
    return {'return': 0}

if __name__ == "__main__":
    i = {'env': os.environ, 'state': {}}
    extractProcess(i)
    outputProcess(i)

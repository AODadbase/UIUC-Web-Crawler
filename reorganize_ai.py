import os
import shutil
import logging
from transformers import pipeline
import re

# ================= High-level configuration for university content =================
# reorganize_ai.py
BASE_DIR = "uiuc_knowledge_base"
# Only send ambiguous files to the AI classifier
TARGET_DIR = os.path.join(BASE_DIR, "uncategorized")
# Detailed natural language labels for the classifier
# Use specific labels for more accurate classification
CANDIDATE_LABELS = [
    # People and organizations
    "university faculty or staff profile",       # Faculty and staff profiles
    "administrative department or office",       # Administrative departments and offices
    "student organization or club",              # Student organizations and clubs

    # Admissions and enrollment
    "university admissions and enrollment information",  # Undergraduate and graduate admissions

    # Academics and instruction
    "academic course syllabus and description",  # Course descriptions and syllabi
    "degree major and minor requirements",       # Degree requirements
    "academic calendar and deadlines",           # Academic calendar and deadlines
    "library resources and databases",           # Library resources

    # Orientation and support
    "new student orientation and academic support services",  # Orientation and academic support

    # Housing and campus life
    "student housing and residence halls",       # Housing and residence halls
    "dining services and menus",                 # Dining services
    "campus parking and transportation",         # Parking and transportation
    "health and wellness services",              # Health and counseling services
    "student immunization and vaccination requirements",  # Immunization requirements
    "campus safety and police",                  # Campus safety and police

    # Student life
    "student life organizations and campus involvement",  # Student life and involvement

    # Athletics and recreation
    "university athletics sports and recreation programs",  # Athletics and recreation

    # International students and scholars (ISSS)
    "international student and scholar services and immigration",  # International services and immigration

    # Finance and career
    "tuition, scholarships and financial aid",   # Tuition and financial aid
    "career services and job opportunities",     # Career services and jobs

    # Research and news
    "scientific research lab or project",        # Research labs and projects
    "university news, press release or story",   # University news and stories
    "public event, workshop or seminar",         # Events and workshops

    # Policies and technology
    "university policy and code of conduct",     # Policies and codes of conduct
    "it support and software services",          # IT support and software services

    # Accessibility and inclusion
    "disability resources and accessibility services",     # Disability and accessibility resources
    "diversity equity and inclusion offices and programs", # Diversity, equity, and inclusion

    # Alumni and giving
    "alumni relations networks and giving opportunities",  # Alumni relations and giving

    # Campus services and logistics
    "campus services logistics and id cards",    # Campus services and ID cards

    # Low-value or error pages
    "login page or access denied error",         # Login-required or access-denied pages
    "website navigation menu or footer",         # Pure navigation or footer pages
    "404 page not found"                         # 404 pages
]

# Map verbose labels to concise directory names
LABEL_TO_DIR = {
    # People and organizations
    "university faculty or staff profile": "people",
    "administrative department or office": "about",
    "student organization or club": "student_life",

    # Academics
    "academic course syllabus and description": "academics",
    "degree major and minor requirements": "academics",
    "academic calendar and deadlines": "events",
    "library resources and databases": "library",

    # Admissions and enrollment
    "university admissions and enrollment information": "admissions",

    # Housing and campus life
    "student housing and residence halls": "housing",
    "dining services and menus": "housing",
    "campus parking and transportation": "transportation",
    "health and wellness services": "health",
    "student immunization and vaccination requirements": "health",
    "campus safety and police": "safety",

    # Orientation and support
    "new student orientation and academic support services": "student_support",

    # Student life and organizations
    "student life organizations and campus involvement": "student_life",

    # Athletics and recreation
    "university athletics sports and recreation programs": "athletics",

    # International students and scholars
    "international student and scholar services and immigration": "isss",

    # Finance and career
    "tuition, scholarships and financial aid": "financial",
    "career services and job opportunities": "career",

    # Research and news
    "scientific research lab or project": "research",
    "university news, press release or story": "news",
    "public event, workshop or seminar": "events",

    # Policies and IT
    "university policy and code of conduct": "policies",
    "it support and software services": "it_services",

    # Accessibility and DEI
    "disability resources and accessibility services": "accessibility",
    "diversity equity and inclusion offices and programs": "diversity",

    # Alumni and giving
    "alumni relations networks and giving opportunities": "alumni",

    # Campus services and logistics
    "campus services logistics and id cards": "campus_services",

    # Low-value or error pages
    "login page or access denied error": "trash",
    "website navigation menu or footer": "trash",
    "404 page not found": "trash"
}

logging.basicConfig(level=logging.INFO)

class AIClassifier:
    def __init__(self):
        print("✅ Loading zero-shot classification model (facebook/bart-large-mnli)... This may take a moment.")
        # Use a zero-shot classification pipeline
        # Classifies text into labels without task-specific training data
        self.classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

    def classify(self, text):
        # Use the first 500 characters for efficiency
        sample_text = text[:500]
        
        result = self.classifier(
            sample_text,
            CANDIDATE_LABELS,
            multi_label=False # Single-label prediction
        )
        
        # Top label and confidence score
        top_label = result['labels'][0]
        confidence = result['scores'][0]
        
        return top_label, confidence
def add_to_blacklist(url):
    """Append a URL to the blacklist file"""
    if not url:
        return
    try:
        with open("blacklist.txt", "a", encoding="utf-8") as f:
            f.write(url + "\n")
        print(f"⚠️ URL added to blacklist: {url}")
    except Exception as e:
        print(f"❌ Failed to update blacklist: {e}")

def extract_url_from_markdown(content):
    """Extract URL from Markdown metadata"""
    # Match lines like: > URL: https://...
    match = re.search(r'> URL: (.*)', content)
    if match:
        return match.group(1).strip()
    return None

def process_files_smartly():
    # Process uncategorized files with AI assistance
    
    for filename in files:
        file_path = os.path.join(TARGET_DIR, filename)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # --- 修改开始 ---
            
            # 1. Basic length filter: blacklist and delete very short content
            if len(content) < 150:
                print(f"⚠️ [Too short] {filename}")
                url = extract_url_from_markdown(content) # Extract URL
                add_to_blacklist(url)                    # Blacklist URL
                os.remove(file_path)                     # Delete file
                stats["deleted"] += 1
                continue

            # 2. AI classification
            label, score = ai.classify(content)
            folder_name = LABEL_TO_DIR[label]
            
            if folder_name == "trash":
                print(f"❌ [Classified as trash] {filename} (confidence: {score:.2f})")
                url = extract_url_from_markdown(content) # Extract URL
                add_to_blacklist(url)                    # Blacklist URL
                os.remove(file_path)                     # Delete file
                stats["deleted"] += 1
        except Exception as e:
            print(f"Error while processing {filename}: {e}")

def add_to_blacklist(url):
    """Append a URL to the blacklist file"""
    if not url:
        return
    try:
        with open("blacklist.txt", "a", encoding="utf-8") as f:
            f.write(url + "\n")
        print(f"⚠️ URL added to blacklist: {url}")
    except Exception as e:
        print(f"❌ Failed to update blacklist: {e}")

def extract_url_from_markdown(content):
    """Extract URL from Markdown metadata"""
    # Match lines like: > URL: https://...
    match = re.search(r'> URL: (.*)', content)
    if match:
        return match.group(1).strip()
    return None

def process_files_smartly():
    if not os.path.exists(TARGET_DIR):
        print("Uncategorized folder not found.")
        return

    ai = AIClassifier()
    files = [f for f in os.listdir(TARGET_DIR) if f.endswith(".md")]
    print(f"✅ AI classifier is ready. Reviewing {len(files)} files...\n")

    stats = {"moved": 0, "deleted": 0, "uncertain": 0}

    for filename in files:
        file_path = os.path.join(TARGET_DIR, filename)
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 1. Basic length filter: blacklist and delete very short content
            if len(content) < 150:
                print(f"⚠️ [Too short] {filename}")
                url = extract_url_from_markdown(content) # Extract URL
                add_to_blacklist(url)                    # Blacklist URL
                os.remove(file_path)                     # Delete file
                stats["deleted"] += 1
                continue

            # 2. AI classification
            label, score = ai.classify(content)
            folder_name = LABEL_TO_DIR[label]
            
            if folder_name == "trash":
                print(f"❌ [Classified as trash] {filename} (confidence: {score:.2f})")
                url = extract_url_from_markdown(content) # Extract URL
                add_to_blacklist(url)                    # Blacklist URL
                os.remove(file_path)                     # Delete file
                stats["deleted"] += 1
            
            elif score > 0.4: # Confidence threshold; adjust as needed
                target_dir = os.path.join(BASE_DIR, folder_name)
                os.makedirs(target_dir, exist_ok=True)
                
                # Move the file to the target directory
                new_path = os.path.join(target_dir, filename)
                shutil.move(file_path, new_path)
                
                print(f"✅ [{folder_name.upper()}] {filename} (confidence: {score:.2f})")
                stats["moved"] += 1
            else:
                print(f"⚠️ [Uncertain] {filename} (top match: {label}, {score:.2f}) -> left in place")
                stats["uncertain"] += 1

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    print("\n" + "="*30)
    print("✅ AI classification complete")
    print(f"Classified files: {stats['moved']}")
    print(f"Deleted as trash: {stats['deleted']}")
    print(f"Still uncertain: {stats['uncertain']}")
    print("="*30)

if __name__ == "__main__":
    process_files_smartly()
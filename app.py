import os
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Gemini API Key loaded in generate_ai_feedback function

def get_repo_details(repo_url):
    """
    Fetches repository details from GitHub API with richer metrics for SDET scoring.
    """
    try:
        parts = repo_url.rstrip('/').split('/')
        if len(parts) < 2 or 'github.com' not in parts[-3]:
            return None
        
        owner = parts[-2]
        repo = parts[-1]
        
        api_base = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {'Accept': 'application/vnd.github.v3+json'}
        
        # 1. Fetch Repository Metadata
        repo_response = requests.get(api_base, headers=headers)
        if repo_response.status_code != 200:
            return None
        repo_data = repo_response.json()
        
        # 2. Fetch Root Contents (Files vs Dirs)
        contents_response = requests.get(f"{api_base}/contents", headers=headers)
        files = []
        dirs = []
        file_sizes = {} # Map filename -> size in bytes
        
        if contents_response.status_code == 200:
            for item in contents_response.json():
                if item['type'] == 'file':
                    files.append(item['name'])
                    file_sizes[item['name']] = item['size']
                elif item['type'] == 'dir':
                    dirs.append(item['name'])
        
        # 3. Check for 'tests' content if tests dir exists
        has_tests = False
        if "tests" in dirs:
            tests_response = requests.get(f"{api_base}/contents/tests", headers=headers)
            if tests_response.status_code == 200:
                # Check for any file starting with 'test_'
                for item in tests_response.json():
                    if item['type'] == 'file' and item['name'].startswith('test_'):
                        has_tests = True
                        break
        # Also check root for test_*.py
        if not has_tests:
            if any(f.startswith('test_') for f in files):
                has_tests = True

        # 4. Check for .github contents (for workflows)
        has_ci = False
        if ".github" in dirs:
            gh_response = requests.get(f"{api_base}/contents/.github", headers=headers)
            if gh_response.status_code == 200:
                # We look for workflows dir or potentially config files
                gh_contents = [i['name'] for i in gh_response.json()]
                if "workflows" in gh_contents:
                    # Assume if workflows dir exists, CI is set up (simplification)
                    has_ci = True
        
        # Also check for other CI files in root
        if not has_ci:
             if any(f in ['.travis.yml', 'circleci/config.yml'] for f in files): # circleci is typically a folder, simplified check
                 has_ci = True
             # Check for circleci folder
             if "circleci" in dirs: 
                 has_ci = True

        # 5. Fetch Commit Count (Last 3 months rough check or just count)
        # We will use the count of the last 100 commits as a proxy for "recent activity/volume"
        commits_response = requests.get(f"{api_base}/commits?per_page=100", headers=headers)
        commit_count = 0
        if commits_response.status_code == 200:
            commit_count = len(commits_response.json())
        
        # 6. Check for Linter Configs
        linter_files = ['.flake8', '.pylintrc', '.eslintrc', '.eslintrc.js', '.eslintrc.json', '.prettierrc']
        has_linter = any(f for f in files if f in linter_files or f.startswith('.eslintrc'))

        return {
            "name": repo_data.get('name'),
            "description": repo_data.get('description'),
            "stars": repo_data.get('stargazers_count'),
            "forks": repo_data.get('forks_count'),
            "files": files,
            "dirs": dirs,
            "file_sizes": file_sizes,
            "commit_count": commit_count,
            "default_branch": repo_data.get('default_branch'),
            "has_tests": has_tests,
            "has_ci": has_ci,
            "has_linter": has_linter,
            "license": repo_data.get('license')
        }
        
    except Exception as e:
        print(f"Error fetching repo details: {e}")
        return None

def calculate_score(metrics):
    """
    Calculates SDET-focused score (0-100).
    """
    score = 0
    files = metrics['files']
    dirs = metrics['dirs']
    
    # --- 1. Testing & Quality (35 pts) ---
    test_score = 0
    # 15 pts: tests/ directory AND test files
    if metrics['has_tests']:
        test_score += 15
    # 10 pts: CI/CD
    if metrics['has_ci']:
        test_score += 10
    # 10 pts: Linter
    if metrics['has_linter']:
        test_score += 10
        
    score += test_score

    # --- 2. Architecture & Structure (25 pts) ---
    arch_score = 0
    # 15 pts: Standard structure (src, app, lib, components) vs Flat
    std_dirs = ['src', 'app', 'lib', 'components', 'pkg']
    if any(d in std_dirs for d in dirs):
        arch_score += 15
    
    # 10 pts: Dependency file
    dep_files = ['requirements.txt', 'package.json', 'pom.xml', 'go.mod', 'Pipfile', 'pyproject.toml']
    if any(f in dep_files for f in files):
        arch_score += 10
        
    score += arch_score

    # --- 3. Git Hygiene (20 pts) ---
    git_score = 0
    # 10 pts: Commit metrics
    cc = metrics['commit_count']
    if cc >= 20:
        git_score += 10
    elif cc >= 5:
        git_score += 5
    else:
        git_score += 0 # < 5 commits
        
    # 10 pts: .gitignore
    if ".gitignore" in files:
        git_score += 10
        
    score += git_score

    # --- 4. Documentation (10 pts) ---
    # 10 pts: Checks for README > 100 bytes
    doc_score = 0
    readme_name = next((f for f in files if f.lower().startswith('readme')), None)
    if readme_name:
        size = metrics['file_sizes'].get(readme_name, 0)
        if size > 100:
            doc_score += 10
    
    score += doc_score

    # --- 5. Metadata (10 pts) ---
    meta_score = 0
    # 5 pts: Description filled
    if metrics['description']:
        meta_score += 5
    # 5 pts: License exists
    if metrics['license']: # GitHub API returns license object if valid
        meta_score += 5
        
    score += meta_score
    
    total_score = min(score, 100)
    
    breakdown = {
        "testing": {"score": test_score, "max": 35, "label": "Testing & Quality"},
        "architecture": {"score": arch_score, "max": 25, "label": "Architecture"},
        "git": {"score": git_score, "max": 20, "label": "Git Hygiene"},
        "docs": {"score": doc_score, "max": 10, "label": "Documentation"},
        "metadata": {"score": meta_score, "max": 10, "label": "Metadata"}
    }
    
    return total_score, breakdown

def get_level_and_medal(score):
    """
    Determines the Level and Medal based on the score.
    """
    if score >= 90:
        return "Advanced", "Gold"
    elif score >= 75:
        return "Intermediate", "Silver"
    else:
        return "Beginner", "Bronze"

def generate_ai_feedback(score, metrics):
    """
    Generates Summary and Roadmap using OpenAI or fallback rules.
    
    Args:
        score (int): The calculated score.
        metrics (dict): Repository metrics.
        
    Returns:
        dict: Contains 'summary' and 'roadmap' strings.
    """
    summary = ""
    roadmap = []
    
    level, medal = get_level_and_medal(score)

    # Gemini API Key (Optional)
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

    if GEMINI_API_KEY:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel('gemini-flash-latest')
            
            prompt = f"""
            You are an AI Coding Mentor. Analyze this GitHub repository for a student hacker.
            
            Metrics:
            - Score: {score}/100 ({level} Level, {medal} Medal)
            - Description: {metrics['description']}
            - Files: {', '.join(metrics['files'][:15])}...
            - Commits (recent): {metrics['commit_count']}
            - Stars: {metrics['stars']}
            
            Provide a JSON response with:
            1. "summary": A 1-sentence evaluation of the code quality, encouraging but honest.
            2. "roadmap": A list of strings, where each string is a specific, actionable step to improve the project. Focus on engineering best practices (CI/CD, Tests, Documentation, Git flow).
            
            Ensure the response is valid JSON.
            """
            
            response = model.generate_content(prompt)
            
            import json
            # Clean up potential markdown code blocks in response
            content_str = response.text.replace('```json', '').replace('```', '').strip()
            content = json.loads(content_str)
            return content
            
        except Exception as e:
            print(f"AI Generation failed: {e}. Using fallback.")
            # Fall through to fallback
            
    # Fallback Logic (Evaluative Tone)
    if score >= 80:
        summary = "Excellent project depth and clean codebase."
        roadmap = [
            "Add automated tests",
            "Improve issue tracking",
            "Contribute project to open-source"
        ]
    elif score >= 50:
        summary = "Strong code consistency and folder structure; needs more tests and documentation."
        roadmap = [
            "Add unit tests",
            "Improve README with project instructions",
            "Introduce CI/CD using GitHub Actions"
        ]
    else:
        summary = "Basic project structure but poor documentation and inconsistent commits."
        roadmap = [
            "Add README with setup instructions",
            "Restructure folders",
            "Commit regularly with meaningful messages"
        ]
        
    return {"summary": summary, "roadmap": roadmap}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    repo_url = data.get('url')
    
    if not repo_url:
        return jsonify({"error": "URL is required"}), 400
        
    metrics = get_repo_details(repo_url)
    
    if not metrics:
        return jsonify({"error": "Invalid GitHub URL or Repository not found"}), 404
        
    score, breakdown = calculate_score(metrics)
    feedback = generate_ai_feedback(score, metrics)
    level, medal = get_level_and_medal(score)
    
    return jsonify({
        "score": score,
        "level": level,
        "medal": medal,
        "summary": feedback['summary'],
        "roadmap": feedback['roadmap'],
        "breakdown": breakdown
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)

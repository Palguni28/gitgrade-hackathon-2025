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
    Fetches repository details from GitHub API.
    
    Args:
        repo_url (str): The full URL of the GitHub repository.
        
    Returns:
        dict: A dictionary containing repo info, commit count, and file structure.
        None: If the URL is invalid or repo not found.
    """
    try:
        # Extract owner and repo name from URL
        parts = repo_url.rstrip('/').split('/')
        if len(parts) < 2 or 'github.com' not in parts[-3]:
            return None
        
        owner = parts[-2]
        repo = parts[-1]
        
        api_base = f"https://api.github.com/repos/{owner}/{repo}"
        headers = {'Accept': 'application/vnd.github.v3+json'}
        
        # Fetch basic repo info
        repo_response = requests.get(api_base, headers=headers)
        if repo_response.status_code != 200:
            return None
        repo_data = repo_response.json()
        
        # Fetch commit activity (last year) - simplified to get a count estimate
        # Note: Getting exact total commits can be expensive via API, using a heuristic or recent activity
        commits_response = requests.get(f"{api_base}/commits?per_page=1", headers=headers)
        commit_count = 0
        if commits_response.status_code == 200:
             # This is a rough way to check if there are commits. 
             # To get total count accurately requires pagination or checking link headers.
             # For this hackathon scope, we'll check the 'Link' header for last page or just assume > 0
             if 'Link' in commits_response.headers:
                 # Parse link header to find last page number for total commits approximation
                 # This is complex to implement perfectly in one go, so we will use a simpler metric:
                 # Just fetch the last 100 commits to see if it's active.
                 commits_list_response = requests.get(f"{api_base}/commits?per_page=100", headers=headers)
                 commit_count = len(commits_list_response.json())
             else:
                 commit_count = len(commits_response.json())

        # Fetch contents to check for specific files
        contents_response = requests.get(f"{api_base}/contents", headers=headers)
        files = []
        if contents_response.status_code == 200:
            files = [item['name'] for item in contents_response.json()]
            
        return {
            "name": repo_data.get('name'),
            "description": repo_data.get('description'),
            "stars": repo_data.get('stargazers_count'),
            "forks": repo_data.get('forks_count'),
            "open_issues": repo_data.get('open_issues_count'),
            "files": files,
            "commit_count": commit_count # This is capped at 100 for this implementation
        }
        
    except Exception as e:
        print(f"Error fetching repo details: {e}")
        return None

def calculate_score(metrics):
    """
    Calculates a heuristic score (0-100) based on repository metrics.
    
    Args:
        metrics (dict): Repository metrics from get_repo_details.
        
    Returns:
        int: Calculated score.
    """
    score = 0
    files = metrics['files']
    
    # 1. README.md existence (30 points)
    if any(f.lower().startswith('readme') for f in files):
        score += 30
        
    # 2. Dependency definition (20 points)
    if any(f in ['.gitignore', 'requirements.txt', 'package.json', 'pom.xml', 'build.gradle'] for f in files):
        score += 20
        
    # 3. Description filled (10 points)
    if metrics['description']:
        score += 10
        
    # 4. Commit activity (Max 20 points)
    # If we found 100+ commits (our cap), full points. Otherwise proportional.
    commit_score = min(metrics['commit_count'], 20)
    score += commit_score
    
    # 5. Community Health (Max 20 points)
    # Simple check for license or contributing guide could go here, 
    # but let's use stars/forks as a proxy for "useful"
    if metrics['stars'] > 5:
        score += 10
    if metrics['forks'] > 0:
        score += 10
        
    return min(score, 100)

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
        
    score = calculate_score(metrics)
    feedback = generate_ai_feedback(score, metrics)
    level, medal = get_level_and_medal(score)
    
    return jsonify({
        "score": score,
        "level": level,
        "medal": medal,
        "summary": feedback['summary'],
        "roadmap": feedback['roadmap']
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)

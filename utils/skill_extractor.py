import re

SKILL_ALIASES = {
    "javascript": "JavaScript", "js": "JavaScript", "typescript": "TypeScript",
    "ts": "TypeScript", "reactjs": "React", "react.js": "React",
    "node": "Node.js", "nodejs": "Node.js", "expressjs": "Express",
    "rest apis": "REST API", "restful api": "REST API", "apis": "REST API",
    "postgres": "PostgreSQL", "mongo": "MongoDB", "ml": "Machine Learning",
    "dl": "Deep Learning", "cv": "OpenCV", "powerbi": "Power BI",
    "scikit learn": "scikit-learn", "sklearn": "scikit-learn",
    "c plus plus": "C++", "ms excel": "Excel",
}

SKILLS = [
    "Python", "Java", "C", "C++", "C#", "JavaScript", "TypeScript", "HTML", "CSS",
    "React", "Angular", "Vue", "Node.js", "Express", "Flask", "Django", "FastAPI",
    "REST API", "SQL", "MySQL", "PostgreSQL", "SQLite", "MongoDB", "Git", "GitHub",
    "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Linux", "TensorFlow", "PyTorch",
    "scikit-learn", "OpenCV", "Pandas", "NumPy", "Power BI", "Excel",
    "Machine Learning", "Deep Learning", "NLP", "Data Analysis", "Data Structures",
    "Algorithms", "OOP", "Computer Networks", "Operating Systems", "DBMS",
    "Cybersecurity", "Cloud Computing", "Agile", "Jira", "Problem Solving",
    "Communication", "Teamwork", "Testing", "Deployment", "Database Design",
    "Version Control", "Communication Skills", "Leadership",
]

KEY_PHRASES = [
    "problem solving", "communication", "teamwork", "code review", "troubleshooting",
    "scalability", "performance optimization", "software development",
    "web development", "backend development", "frontend development", "full stack",
    "api integration", "database design", "version control", "testing", "deployment",
    "cloud computing", "data analysis", "machine learning", "agile development",
]


def _normalise(text):
    text = text.lower()
    for old, new in SKILL_ALIASES.items():
        text = text.replace(old, new.lower())
    return text


def _contains(text, phrase):
    return re.search(
        r"(?<![a-z0-9+#.-])" + re.escape(phrase.lower()) + r"(?![a-z0-9+#.-])",
        text
    ) is not None


def extract_skills(text):
    normalised = _normalise(text)
    return sorted(
        {skill for skill in SKILLS if _contains(normalised, skill)},
        key=str.lower
    )


def extract_keywords(text):
    normalised = _normalise(text)
    found = {skill for skill in SKILLS if _contains(normalised, skill)}
    found.update(phrase.title() for phrase in KEY_PHRASES if phrase in normalised)
    return sorted(found, key=str.lower)
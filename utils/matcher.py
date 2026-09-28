def calculate_match(resume_skills, required_skills, resume_text="", job_description=""):
    resume_map = {x.lower(): x for x in resume_skills}
    required = []
    seen = set()
    for skill in required_skills:
        key = skill.lower()
        if key not in seen:
            seen.add(key)
            required.append(skill)

    matching = [x for x in required if x.lower() in resume_map]
    missing = [x for x in required if x.lower() not in resume_map]

    skill_score = (len(matching) / len(required) * 100) if required else 0

    # Extra intelligent signals: keyword coverage + resume completeness.
    job_words = {w.lower() for w in job_description.split() if len(w) > 4}
    resume_words = {w.lower() for w in resume_text.split() if len(w) > 4}
    keyword_coverage = (
        len(job_words & resume_words) / len(job_words) * 100
        if job_words else 0
    )

    sections = ["education", "experience", "projects", "skills", "summary"]
    lower_resume = resume_text.lower()
    completeness = sum(s in lower_resume for s in sections) / len(sections) * 100

    if required:
        score = round(skill_score * 0.75 + keyword_coverage * 0.15 + completeness * 0.10)
    else:
        score = round(completeness * 0.5 + keyword_coverage * 0.5)

    return min(100, max(0, score)), matching, missing, round(skill_score), round(keyword_coverage), round(completeness)
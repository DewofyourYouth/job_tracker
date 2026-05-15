Date: {{ date }}

--- CV ---
{{ cv_text }}

--- PROFILE ---
{{ profile_text }}

--- LISTING ---
Title:       {{ title }}
Company:     {{ company }}
URL:         {{ url }}
Location:    {{ location }}
Salary hint: {{ salary_hint }}
Rule scores: {{ rule_scores }}
Total rule:  {{ total_score }}

Quick LLM evaluation (for calibration — do not copy verbatim):
  fit_score: {{ fit_score }}/10
  summary: {{ fit_summary }}
  strengths: {{ strengths }}
  red_flags: {{ red_flags }}

--- JOB DESCRIPTION ---
{{ description }}

TASK: Write a detailed job-fit report for this listing. Return JSON only.

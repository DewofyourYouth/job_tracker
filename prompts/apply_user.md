## Target Role

**Title:** {{ job_title }}
**Company:** {{ company }}
**Location:** {{ location }}
**Salary:** {{ salary }}

## Job Description

{{ description }}

{% if fit_summary %}
## Prior Evaluation Notes

**Fit summary:** {{ fit_summary }}
{% if strengths %}**Strengths:** {{ strengths }}{% endif %}
{% if red_flags %}**Red flags:** {{ red_flags }}{% endif %}
{% endif %}

---

## Candidate CV

{{ cv_text }}

---

## Candidate Profile

{{ profile_text }}

---

Generate the tailored application JSON now. Tailor the CV for this specific role. Write a specific, grounded cover letter. Output only the JSON object.

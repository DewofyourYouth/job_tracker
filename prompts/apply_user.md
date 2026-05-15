## Target Role

**Title:** {{ job_title }}
**Company:** {{ company }}
**Location:** {{ location }}
**Salary:** {{ salary }}

## Job Description

{{ description }}
{% if description.endswith('...') %}
*(Description truncated — base the application only on the context above.)*
{% endif %}

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

{% if include_cover_letter %}
Generate the tailored application JSON now. Tailor the CV for this specific role. Write a specific, grounded cover letter. Output only the JSON object.
{% else %}
Generate the tailored application JSON now. Tailor the CV for this specific role. Do NOT generate the cover_letter field — omit it from the JSON entirely. Output only the JSON object.
{% endif %}

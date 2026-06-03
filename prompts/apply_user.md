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

{% if positioning %}
## Positioning directive (follow precisely)

This candidate's positioning is encoded in data/positioning.yaml. For THIS role:

- CV subtitle: use exactly — "{{ archetype_headline }}" — unless it is clearly wrong for the listing, in which case adapt minimally. Never output a bare "Senior Backend Engineer".
- Lead pillar: {{ lead_pillar_label }}{% if lead_pillar_oneliner %} — {{ lead_pillar_oneliner }}{% endif %}
  Lead the professional summary and the strongest experience bullet with this.
{% if support_pillars_labels %}- Support pillars (weave in after the lead): {{ support_pillars_labels | join('; ') }}
{% endif %}{% if emphasis %}- Role emphasis: {{ emphasis }}
{% endif %}{% if lead_proof_points %}- Canonical proof points to draw from (use the real ones; do not embellish):
{% for p in lead_proof_points %}  - {{ p }}
{% endfor %}{% endif %}
### Non-negotiable guardrails
{% for g in guardrails %}- {{ g }}
{% endfor %}
---

{% endif %}
## Instructions

Before writing anything, read the candidate profile's `narrative.superpowers` and `narrative.proof_points`. These are the candidate's pre-ranked differentiators. Lead with whichever of them is most relevant to the target role above.

Key framing rules for this candidate:
- If the CV shows a tech lead role (owned architecture, no PM layer, direct client communication), frame it explicitly as **Technical Lead** — not "manager" and not hedged. State the team size and scope directly.
- If the role is in fintech, security, infrastructure, or any domain where reliability matters: the air-gapped / 60-country / government-deployment proof point is the anchor — lead with it in both the summary and the strongest experience bullet.
- If the role is remote-international, reference timezone overlap (4–5h US East Coast) and multilingual capability where relevant.
- The exit story (leaving when the role became pure DevOps post-acquisition) is only for the cover letter close if a forward-looking pivot is appropriate — never put it in CV bullets.

{% if include_cover_letter %}
Generate the tailored application JSON now. Tailor the CV for this specific role. Write a specific, grounded cover letter per the system prompt guidance. Output only the JSON object.
{% else %}
Generate the tailored application JSON now. Tailor the CV for this specific role. Do NOT generate the cover_letter field — omit it from the JSON entirely. Output only the JSON object.
{% endif %}

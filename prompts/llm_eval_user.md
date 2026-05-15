CANDIDATE:
  target_roles: {{ target_roles }}
  avoid: {{ avoid_roles }}
  acceptable_locations: {{ acceptable_locs }}
  compensation: min {{ min_comp }} {{ currency }}, target {{ target_comp }} {{ currency }}
{% if candidate_summary %}
  profile: |
    {{ candidate_summary | indent(4) }}
{% endif %}
{% if tech_stack %}
  tech_stack: {{ tech_stack }}
{% endif %}

LISTING:
  title: {{ title }}
  company: {{ company }}
  location: {{ location }}
  salary_hint: {{ salary_hint }}
  rule_scores: {{ rule_scores }}
  total_rule_score: {{ total_score }}
  description: |
    {{ description }}

TASK: Evaluate fit. Return JSON only.

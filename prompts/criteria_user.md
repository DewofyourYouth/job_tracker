generated_at: {{ generated_at }}

--- CV (Markdown) ---
{{ cv_text }}

--- Profile (YAML) ---
{{ profile_text }}
{% if portals_yaml %}

--- portals.yaml title_filter context ---
{{ portals_yaml }}
{% endif %}

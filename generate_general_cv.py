#!/usr/bin/env python3
"""Generate a general/stealth CV for the hire-me page on jacob-shore.com.

What's scrubbed vs. kept:
  - Phone number: OMITTED (replaced with GitHub link in contact row)
  - Integrity Labs: named as "Security-Focused Technology Firm" per request
  - Location: "Israel | Open to remote" (no street-level detail)
  - Email, LinkedIn, GitHub, website: retained (public lead-capture info)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from commands.apply import (
    _esc,
    _fill_template,
    _render_certifications,
    _render_competencies,
    _render_education,
    _render_experience,
    _render_projects,
    _render_skills,
    TEMPLATES_DIR,
)

OUTPUT_DIR = Path("output/general")

# ---------------------------------------------------------------------------
# CV data — stealth / general version
# ---------------------------------------------------------------------------

DATA: dict = {
    "subtitle": "Senior Backend & Platform Engineer | Python · Go · Kubernetes · GitOps",
    "summary": (
        "Senior backend and platform engineer with 8+ years building secure, high-reliability distributed systems "
        "across backend services, deployment architecture, and platform tooling in cybersecurity, fintech, and "
        "regulated infrastructure. Most recently served as hands-on technical lead for a four-engineer pod at a "
        "security-focused B2G technology firm — shipping customer-facing and backend systems for government clients "
        "in 60+ countries inside air-gapped, self-hosted environments where a bad deployment cannot be hotfixed "
        "over the public internet. Strongest at the intersection of backend engineering and deployment: distributed "
        "services, Kubernetes-based infrastructure, customer-managed deployments, and reliability-first systems. "
        "Multilingual: English (native), Hebrew (fluent/professional), Arabic (conversational). "
        "Israel-based; open to remote."
    ),
    "competencies": [
        "Python (FastAPI, Flask, Django)",
        "Go (GoFiber)",
        "Kubernetes",
        "GitOps / ArgoCD",
        "Air-Gapped Deployment",
        "Distributed Systems",
        "Technical Leadership",
        "PostgreSQL · MongoDB · Redis",
        "Docker",
        "CI/CD Pipelines",
        "Node.js",
        "React / TypeScript",
    ],
    "experience": [
        {
            "company": "Security-Focused Technology Firm",
            "period": "July 2022 – December 2025",
            "role": "Backend & Infrastructure Engineer — Technical Lead",
            "location": "Tel Aviv, Israel",
            "bullets": [
                "Replaced a manual physical-courier government update process with a fully automated remote pipeline — "
                "React frontend, Python (FastAPI) and Go (GoFiber) backend, Kubernetes/ArgoCD/GitOps deployment — "
                "operating inside air-gapped sovereign networks across 60+ countries, cutting update time from weeks to under an hour",
                "Designed and operated the platform deployment architecture across 60+ heterogeneous customer environments: "
                "air-gapped Kubernetes clusters, Consul KV for configuration, PostgreSQL and MongoDB for state, Redis for "
                "transport — built to roll forward and roll back without external dependencies",
                "Served as technical lead for a 4-engineer team with no PM layer — owned architecture, set technical strategy, "
                "ran code review, broke down and assigned work, and contributed hands-on code in the core path "
                "(Python services, Kubernetes operators, CI/CD pipelines)",
                "Owned the engineering-to-client interface: translated foreign-government analyst requirements into architecture "
                "decisions, ran technical reviews directly with customer security teams, and shipped to production on a quarterly "
                "cadence with zero unplanned downtime events",
                "Defined engineering standards across testing, deployment, and operational practices for multi-language services "
                "running on self-hosted Kubernetes infrastructure under strict security and operational constraints",
            ],
        },
        {
            "company": "IronScales",
            "period": "2021 – 2022",
            "role": "Backend Engineer",
            "location": "Ramat Gan, Israel",
            "bullets": [
                "Built and maintained Django-based APIs for a high-availability enterprise cybersecurity SaaS platform "
                "serving large commercial clients",
                "Collaborated with infrastructure teams on deployment, scaling, and operational concerns for a "
                "security-critical system where uptime guarantees had direct customer impact",
            ],
        },
        {
            "company": "DatAlign",
            "period": "2019 – 2021",
            "role": "Backend & Infrastructure Engineer",
            "location": "Beit Shemesh, Israel",
            "bullets": [
                "Built Docker-based HIPAA-compliant deployment systems and CI/CD pipelines for regulated healthcare "
                "infrastructure — owned the deployment architecture end-to-end",
                "Owned the customer-facing React frontend end-to-end alongside full backend responsibilities — "
                "sole owner of both product layers without a dedicated frontend engineer",
                "Introduced infrastructure-as-code practices standardizing deployments across environments",
                "Developed Flask backend services emphasizing reliability and operational clarity",
            ],
        },
        {
            "company": "Freelance & Contract Work",
            "period": "2016 – 2019",
            "role": "Backend / Infrastructure Engineer",
            "location": "",
            "bullets": [
                "Built Docker image templates and deployment automation pipelines for enterprise clients",
                "Implemented containerization and infrastructure automation for production systems",
            ],
        },
    ],
    "projects": [
        {
            "title": "HAKI",
            "badge": "",
            "description": (
                "Dialect-aware Arabic learning application with spaced-repetition vocabulary workflows — "
                "built end-to-end (backend, frontend, hosting, real users). Demonstrates product ownership "
                "outside employment and Arabic language depth."
            ),
            "tech": "React, backend API, spaced-repetition algorithm",
        },
        {
            "title": "Semantic Retrieval Pipeline",
            "badge": "",
            "description": (
                "Local RAG pipeline over a ~100K-document markdown archive: sentence-transformers embeddings, "
                "pgvector storage, hybrid keyword + vector retrieval, evaluated with recall@k."
            ),
            "tech": "Python, sentence-transformers, pgvector, PostgreSQL",
        },
    ],
    "education": [
        {"degree": "BA", "institution": "Rabbinical Academy of Yeshivat Rabbeinu Chaim Berlin", "year": ""},
        {"degree": "AS", "institution": "New England Institute of Technology", "year": ""},
    ],
    "certifications": [
        {"title": "JavaScript Algorithms", "issuer": "freeCodeCamp", "year": ""},
        {"title": "Python Programming", "issuer": "Wesleyan / Coursera", "year": ""},
    ],
    "skills": [
        {
            "category": "Infrastructure & Platform",
            "skills": "Kubernetes, Docker, ArgoCD, GitOps, CI/CD, Consul KV, Linux, Air-Gapped Deployment, Infrastructure Automation",
        },
        {
            "category": "Backend Engineering",
            "skills": "Python (FastAPI, Flask, Django), Go (GoFiber), Node.js, .NET/C#",
        },
        {
            "category": "Data & Storage",
            "skills": "PostgreSQL, MongoDB, Redis, Neo4j",
        },
        {
            "category": "Frontend",
            "skills": "React, TypeScript",
        },
        {
            "category": "AI & Retrieval",
            "skills": "Semantic Retrieval, Embeddings (sentence-transformers), pgvector, RAG Pipeline Design, Vector Search",
        },
    ],
}

# ---------------------------------------------------------------------------
# Stealth profile — no phone, GitHub replaces it in the contact row
# ---------------------------------------------------------------------------

PROFILE: dict = {
    "candidate": {
        "full_name": "Jacob Shore",
        "email": "jacobshore@gmail.com",
        "phone": "",
        "location": "Israel",
        "linkedin": "https://www.linkedin.com/in/jacob-shore-86094725a/",
        "portfolio_url": "https://jacob-shore.com",
        "github": "https://github.com/DewofyourYouth",
    }
}


_ICON_GITHUB = (
    '<svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor" style="vertical-align:-1px">'
    '<path fill-rule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17'
    ".55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68"
    "-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64"
    "-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27"
    " 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15"
    " 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013"
    ' 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>'
)

_ICON_LINKEDIN = (
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="vertical-align:-1px">'
    '<path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136'
    " 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267"
    " 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0"
    " 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225"
    " 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24"
    ' 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>'
)

_ICON_EMAIL = (
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="vertical-align:-1px">'
    '<path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8'
    ' 5-8-5V6l8 5 8-5v2z"/></svg>'
)

_ICON_GLOBE = (
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="vertical-align:-1px">'
    '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49'
    "-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9"
    "-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5"
    ' 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg>'
)

_ICON_PIN = (
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" style="vertical-align:-1px">'
    '<path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38'
    ' 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg>'
)

_CONTACT_EXTRA_CSS = """
  .contact-row {
    flex-wrap: nowrap;
    align-items: center;
    gap: 4px 12px;
  }
  .contact-item {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    white-space: nowrap;
  }

"""


def _render_stealth_contact(candidate: dict) -> str:
    """Build a one-line icon contact row + an availability chip."""
    github_url = _esc(candidate.get("github", ""))
    github_handle = "@" + candidate.get("github", "").rstrip("/").split("/")[-1]

    email = _esc(candidate.get("email", ""))
    email_href = f"mailto:{email}"

    linkedin_url = candidate.get("linkedin", "")
    li_handle = linkedin_url.rstrip("/").split("/in/")[-1] if "/in/" in linkedin_url else linkedin_url
    linkedin_url_esc = _esc(linkedin_url)

    portfolio_url = candidate.get("portfolio_url", "")
    portfolio_display = portfolio_url.replace("https://", "").rstrip("/")
    portfolio_url_esc = _esc(portfolio_url)

    sep = '<span class="separator">|</span>'

    items = [
        f'<a class="contact-item" href="{github_url}">{_ICON_GITHUB} {_esc(github_handle)}</a>',
        f'<a class="contact-item" href="{email_href}">{_ICON_EMAIL} {email}</a>',
        f'<a class="contact-item" href="{linkedin_url_esc}">{_ICON_LINKEDIN} {_esc(li_handle)}</a>',
        f'<a class="contact-item" href="{portfolio_url_esc}">{_ICON_GLOBE} {_esc(portfolio_display)}</a>',
        f'<span class="contact-item">{_ICON_PIN} Israel</span>',
    ]

    return f"\n      {(chr(10) + '      ' + sep + chr(10) + '      ').join(items)}\n    "


def render_stealth_cv_html(data: dict, profile: dict) -> str:
    candidate = profile.get("candidate", {})
    template = (TEMPLATES_DIR / "cv-template.html").read_text(encoding="utf-8")

    tokens = {
        "LANG": "en",
        "NAME": _esc(candidate.get("full_name", "")),
        "SUBTITLE": _esc(data.get("subtitle", "")),
        "PHONE": "",
        "EMAIL": _esc(candidate.get("email", "")),
        "LINKEDIN_URL": candidate.get("linkedin", ""),
        "LINKEDIN_DISPLAY": (
            "/" + candidate["linkedin"].split("linkedin.com", 1)[-1].strip("/")
            if "linkedin.com" in candidate.get("linkedin", "")
            else candidate.get("linkedin", "")
        ),
        "PORTFOLIO_URL": candidate.get("portfolio_url", ""),
        "PORTFOLIO_DISPLAY": candidate.get("portfolio_url", "").replace("https://", "").rstrip("/"),
        "LOCATION": candidate.get("location", ""),
        "PAGE_WIDTH": "210mm",
        "SECTION_SUMMARY": "Professional Summary",
        "SUMMARY_TEXT": _esc(data.get("summary", "")),
        "SECTION_COMPETENCIES": "Core Competencies",
        "COMPETENCIES": _render_competencies(data.get("competencies") or []),
        "SECTION_EXPERIENCE": "Professional Experience",
        "EXPERIENCE": _render_experience(data.get("experience") or []),
        "SECTION_PROJECTS": "Projects",
        "PROJECTS": _render_projects(data.get("projects") or []),
        "SECTION_EDUCATION": "Education",
        "EDUCATION": _render_education(data.get("education") or []),
        "SECTION_CERTIFICATIONS": "Certifications",
        "CERTIFICATIONS": _render_certifications(data.get("certifications") or []),
        "SECTION_SKILLS": "Technical Skills",
        "SKILLS": _render_skills(data.get("skills") or []),
    }

    html = _fill_template(template, tokens)

    # Replace the entire contact-row content with the stealth version
    # (removes empty phone span and adds GitHub link)
    contact_html = _render_stealth_contact(candidate)
    html = re.sub(
        r'(<div class="contact-row">).*?(</div>)',
        lambda m: m.group(1) + contact_html + m.group(2),
        html,
        count=1,
        flags=re.DOTALL,
    )

    # Inject icon/chip styles just before </style>
    html = html.replace("</style>", f"  {_CONTACT_EXTRA_CSS}</style>", 1)

    return html


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "jacob-shore-general-cv.html"
    html = render_stealth_cv_html(DATA, PROFILE)
    out_path.write_text(html, encoding="utf-8")
    print(f"✓ General CV → {out_path}")

    # Optional PDF via Playwright
    if "--pdf" in sys.argv:
        from commands.apply import html_to_pdf
        pdf_path = out_path.with_suffix(".pdf")
        if html_to_pdf(out_path, pdf_path):
            print(f"✓ PDF       → {pdf_path}")
        else:
            print("  PDF export failed (is Playwright installed?)")


if __name__ == "__main__":
    main()

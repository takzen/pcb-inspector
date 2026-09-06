"""Interactive standalone HTML report generator with dark theme and interactive filtering."""

from __future__ import annotations

import html

from pcb_inspector.core.models import AuditResult, Severity
from pcb_inspector.reporters.base import BaseReporter


class HtmlReporter(BaseReporter):
    """Produces interactive, self-contained HTML5 reports with offline dark theme."""

    def render(self, result: AuditResult) -> str:
        s = result.summary

        # Color and label for health score
        if s.health_score >= 90:
            score_color = "#238636"  # Emerald Green
            score_label = "EXCELLENT"
        elif s.health_score >= 70:
            score_color = "#d29922"  # Amber
            score_label = "REVIEW RECOMMENDED"
        else:
            score_color = "#da3633"  # Ruby Red
            score_label = "ACTION REQUIRED"

        status_text = "PASSED" if s.passed else "FAILED"
        status_color = "#238636" if s.passed else "#da3633"

        # Generate findings HTML cards
        cards_html: list[str] = []
        for idx, f in enumerate(result.findings, start=1):
            sev_color = {
                Severity.CRITICAL: "#da3633",
                Severity.WARNING: "#d29922",
                Severity.SUGGESTION: "#388bfd",
                Severity.PASS: "#238636",
            }.get(f.severity, "#8b949e")

            comps_html = "".join(f'<span class="chip chip-comp">{html.escape(c)}</span>' for c in f.components)
            nets_html = "".join(f'<span class="chip chip-net">{html.escape(n)}</span>' for n in f.nets)
            coords_str = ", ".join(str(c) for c in f.coordinates) or "N/A"

            # Correlated findings badges
            correlated_html = ""
            if f.correlated_with:
                corrs = "".join(f'<span class="chip chip-corr">🔗 {html.escape(cid)}</span>' for cid in f.correlated_with)
                correlated_html = f'<div class="finding-meta-row"><strong>Correlated Issues:</strong> {corrs}</div>'

            # Actionable fix block
            fix_html = ""
            if f.actionable_fix:
                af = f.actionable_fix
                fix_html = f"""
                <div class="fix-box">
                    <div class="fix-header">
                        <span class="fix-badge">⚡ Actionable Fix</span>
                        <code>{html.escape(af.action_type)}</code>
                    </div>
                    <div class="fix-desc">{html.escape(af.description)}</div>
                </div>
                """

            rationale_html = ""
            if f.rationale:
                rationale_html = f'<div class="box box-rationale"><strong>💡 Physics & Rationale:</strong> {html.escape(f.rationale)}</div>'

            recommendation_html = ""
            if f.recommendation:
                recommendation_html = f'<div class="box box-rec"><strong>🛠️ Recommendation:</strong> {html.escape(f.recommendation)}</div>'

            card = f"""
            <div class="card finding-card" data-severity="{f.severity.value}">
                <div class="card-header">
                    <div class="header-left">
                        <span class="badge" style="background-color: {sev_color}; color: #ffffff;">{f.severity.badge_emoji} {f.severity.value}</span>
                        <span class="badge badge-rule">{html.escape(f.rule_id)}</span>
                        <span class="badge badge-cat">{html.escape(f.category.value)}</span>
                        <span class="finding-id">#{idx} ({html.escape(f.id)})</span>
                    </div>
                </div>
                <h3 class="finding-title">{html.escape(f.title)}</h3>
                <p class="finding-desc">{html.escape(f.description)}</p>

                <div class="finding-meta-grid">
                    <div><strong>Components:</strong> {comps_html or '<span class="text-muted">None</span>'}</div>
                    <div><strong>Nets:</strong> {nets_html or '<span class="text-muted">None</span>'}</div>
                    <div><strong>Coordinates:</strong> <code>{html.escape(coords_str)}</code></div>
                </div>

                {correlated_html}
                {rationale_html}
                {recommendation_html}
                {fix_html}
            </div>
            """
            cards_html.append(card)

        all_cards = "\n".join(cards_html) if cards_html else '<div class="empty-state">🟢 All inspection checks passed. No defects detected.</div>'

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PCB Inspection Report — {html.escape(result.project_path)}</title>
<style>
:root {{
    --bg: #090d16;
    --card-bg: #111827;
    --border: #1f2937;
    --text: #f3f4f6;
    --text-muted: #9ca3af;
    --cyan: #00F0FF;
    --blue: #388bfd;
    --green: #238636;
    --red: #da3633;
    --yellow: #d29922;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: var(--bg);
    color: var(--text);
    line-height: 1.5;
    padding: 2rem;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
.header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2rem;
}}
.header-brand {{ display: flex; align-items: center; gap: 1rem; }}
.header-brand svg {{ width: 50px; height: 50px; }}
.header-title {{ font-size: 1.5rem; font-weight: 700; color: #ffffff; }}
.header-subtitle {{ font-size: 0.875rem; color: var(--text-muted); }}

.metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2rem;
}}
.metric-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.25rem;
    text-align: center;
}}
.metric-val {{ font-size: 2rem; font-weight: 800; }}
.metric-lbl {{ font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; margin-top: 0.25rem; letter-spacing: 0.05em; }}

.filter-bar {{
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}}
.filter-btn {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 0.5rem 1rem;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.875rem;
    font-weight: 600;
    transition: all 0.15s;
}}
.filter-btn:hover, .filter-btn.active {{
    background: var(--border);
    border-color: var(--cyan);
    color: #ffffff;
}}

.card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 1.25rem;
    transition: transform 0.1s ease;
}}
.card:hover {{ border-color: #374151; }}
.card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }}
.header-left {{ display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }}

.badge {{
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 700;
}}
.badge-rule {{ background: #1e293b; color: var(--cyan); border: 1px solid #334155; }}
.badge-cat {{ background: #132338; color: #60a5fa; }}
.finding-id {{ color: var(--text-muted); font-size: 0.8rem; font-family: monospace; }}
.finding-title {{ font-size: 1.15rem; font-weight: 700; margin-bottom: 0.5rem; color: #ffffff; }}
.finding-desc {{ color: #d1d5db; margin-bottom: 1rem; font-size: 0.95rem; }}

.finding-meta-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 0.75rem;
    background: #0d121f;
    padding: 0.75rem 1rem;
    border-radius: 6px;
    font-size: 0.85rem;
    margin-bottom: 0.75rem;
}}
.chip {{
    display: inline-block;
    padding: 0.1rem 0.4rem;
    border-radius: 3px;
    font-size: 0.75rem;
    margin-right: 0.25rem;
    font-family: monospace;
}}
.chip-comp {{ background: #1f2937; color: #fbbf24; }}
.chip-net {{ background: #1f2937; color: #34d399; }}
.chip-corr {{ background: #374151; color: #93c5fd; }}

.box {{
    padding: 0.75rem 1rem;
    border-radius: 6px;
    font-size: 0.875rem;
    margin-top: 0.75rem;
}}
.box-rationale {{ background: #0c1a2e; border-left: 3px solid var(--blue); color: #bfdbfe; }}
.box-rec {{ background: #062419; border-left: 3px solid var(--green); color: #a7f3d0; }}

.fix-box {{
    background: #171c28;
    border: 1px solid #2d3748;
    border-radius: 6px;
    padding: 0.75rem 1rem;
    margin-top: 0.75rem;
}}
.fix-header {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.25rem; }}
.fix-badge {{ font-size: 0.75rem; font-weight: 800; color: #fbbf24; text-transform: uppercase; }}
.fix-desc {{ font-size: 0.875rem; color: #e2e8f0; }}

.empty-state {{
    text-align: center;
    padding: 4rem 2rem;
    background: var(--card-bg);
    border: 1px dashed var(--border);
    border-radius: 8px;
    color: var(--green);
    font-size: 1.25rem;
    font-weight: 600;
}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <div class="header-brand">
            <svg viewBox="0 0 180 180" fill="none">
                <polygon points="90,10 145,35 170,90 145,145 90,170 35,145 10,90 35,35" fill="#0A0F1D" stroke="#00F0FF" stroke-width="4"/>
                <circle cx="90" cy="90" r="46" stroke="#F59E0B" stroke-width="3" stroke-dasharray="6,6"/>
                <rect x="68" y="68" width="44" height="44" rx="4" fill="#1E293B" stroke="#00F0FF" stroke-width="2"/>
                <circle cx="90" cy="90" r="8" fill="#00F0FF"/>
            </svg>
            <div>
                <div class="header-title">pcb-inspector Report</div>
                <div class="header-subtitle">{html.escape(result.project_path)} • v{result.tool_version}</div>
            </div>
        </div>
        <div style="text-align: right;">
            <div style="font-size: 1.5rem; font-weight: 800; color: {status_color};">{status_text}</div>
            <div style="font-size: 0.8rem; color: var(--text-muted);">{result.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
        </div>
    </div>

    <div class="metrics-grid">
        <div class="metric-card" style="border-color: {score_color};">
            <div class="metric-val" style="color: {score_color};">{s.health_score:.0f}<span style="font-size: 1rem;">/100</span></div>
            <div class="metric-lbl">Health Score ({score_label})</div>
        </div>
        <div class="metric-card">
            <div class="metric-val" style="color: #da3633;">{s.critical_count}</div>
            <div class="metric-lbl">Critical</div>
        </div>
        <div class="metric-card">
            <div class="metric-val" style="color: #d29922;">{s.warning_count}</div>
            <div class="metric-lbl">Warning</div>
        </div>
        <div class="metric-card">
            <div class="metric-val" style="color: #388bfd;">{s.suggestion_count}</div>
            <div class="metric-lbl">Suggestion</div>
        </div>
        <div class="metric-card">
            <div class="metric-val">{s.duration_seconds:.2f}s</div>
            <div class="metric-lbl">Duration</div>
        </div>
    </div>

    <div class="filter-bar">
        <button class="filter-btn active" onclick="filterSev('ALL')">All ({s.total_findings})</button>
        <button class="filter-btn" onclick="filterSev('CRITICAL')">🔴 Critical ({s.critical_count})</button>
        <button class="filter-btn" onclick="filterSev('WARNING')">🟠 Warning ({s.warning_count})</button>
        <button class="filter-btn" onclick="filterSev('SUGGESTION')">🟡 Suggestion ({s.suggestion_count})</button>
        <button class="filter-btn" onclick="filterSev('PASS')">🟢 Pass ({s.pass_count})</button>
    </div>

    <div id="findings-list">
        {all_cards}
    </div>
</div>

<script>
function filterSev(sev) {{
    const cards = document.querySelectorAll('.finding-card');
    const btns = document.querySelectorAll('.filter-btn');
    btns.forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');

    cards.forEach(card => {{
        if (sev === 'ALL' || card.getAttribute('data-severity') === sev) {{
            card.style.display = 'block';
        }} else {{
            card.style.display = 'none';
        }}
    }});
}}
</script>
</body>
</html>
"""

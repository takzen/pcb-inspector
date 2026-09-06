# 🔍 pcb-inspector

> **Automated Multimodal Design Reviewer & Linter for KiCad Projects**
> Catch placement flaws, decoupling issues, routing problems, and mixed-signal design risks before sending your board to fabrication.

---

## 🎯 What is it?

`pcb-inspector` is a multi-layer hardware verification and design-review pipeline for KiCad projects.

Traditional DRC/ERC tools are essential, but they primarily verify explicit electrical and geometric rules. `pcb-inspector` adds higher-level **layout analysis, engineering heuristics, and multimodal visual review** to identify potential design issues that may pass standard CAD checks.

It can run standalone as a **CLI or GitHub Action** on manually designed boards, or integrate with agentic PCB workflows such as **Konnect** to create a closed-loop design → audit → fix → verify workflow.

---

## 🧠 Multi-Layer Verification Pipeline

### 1. Deterministic Ground Truth

Runs KiCad's native verification tools through `kicad-cli`:

- ERC — Electrical Rules Check
- DRC — Design Rules Check
- Connectivity verification
- Clearance and short-circuit detection
- Unconnected items
- Manufacturing-related rule violations

These results form the deterministic baseline for the audit.

---

### 2. Programmatic Design Analysis

Computes configurable spatial, geometric, and electrical heuristics from the schematic and PCB:

- Decoupling capacitor placement relative to IC power pins
- Power and ground connectivity
- DC/DC converter switching-loop geometry
- High-current path analysis
- Critical net routing
- Ground-plane and return-path topology
- Analog/digital domain interactions
- Differential-pair length and matching constraints
- Trace width and via usage
- Component placement and proximity
- Thermal and high-power component relationships

Design rules and thresholds are configurable rather than hard-coded, allowing the analysis to adapt to different board types and design requirements.

---

### 3. Multimodal Visual Review

Uses Vision-capable models to inspect rendered **2D and 3D PCB views** and identify higher-level layout issues that are difficult to express as traditional CAD rules.

Examples include:

- Poor component placement and clustering
- Unnecessary routing detours
- Suspicious serpentine or unnecessarily long traces
- Potentially problematic routing patterns
- Crowded or difficult-to-manufacture areas
- Silkscreen overlaps and unreadable reference designators
- Missing or unclear polarity/orientation markings
- Mechanical and thermal layout concerns
- Visual inconsistencies between schematic intent and PCB implementation

Vision analysis is treated as a **heuristic engineering review**, not as a replacement for deterministic electrical or manufacturing checks.

---

### 4. Structured, Actionable Feedback

Produces a prioritized Markdown report:

- 🔴 `CRITICAL`
- 🟠 `WARNING`
- 🟡 `SUGGESTION`
- 🟢 `PASS`

Findings can include:

- Component references
- Net names
- PCB coordinates
- Relevant measurements
- Evidence from DRC/ERC
- Visual evidence
- Explanation of the potential issue
- Recommended corrective action

The output is designed to be useful both for **human engineers and AI agents** performing automated design iteration.

---

## 🔄 Closed-Loop Agent Integration

`pcb-inspector` can act as an independent verification layer inside an agentic PCB workflow:

```text
┌──────────────────────┐
│   Design Agent       │
│   e.g. Konnect       │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   KiCad Project      │
│   Schematic + PCB    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│    pcb-inspector     │
│                      │
│  ├─ ERC / DRC        │
│  ├─ Metrics          │
│  ├─ Connectivity     │
│  └─ Vision Review    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Structured Findings │
│  + Fix Recommendations│
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   Design Agent       │
│   Applies Fixes      │
└──────────┬───────────┘
           │
           └──────────────► Re-audit
```

The goal is a repeatable:

**Design → Inspect → Fix → Verify**

loop.

---

## 🏗️ Design Philosophy

`pcb-inspector` deliberately separates verification into three layers:

| Layer                     | Purpose                                 | Authority                    |
| ------------------------- | --------------------------------------- | ---------------------------- |
| **KiCad DRC/ERC**         | Explicit electrical & geometric rules   | Deterministic                |
| **Programmatic Analysis** | Measurable engineering heuristics       | Deterministic / configurable |
| **Vision Review**         | Higher-level visual & layout heuristics | Probabilistic                |

No single layer is expected to catch everything.

The objective is to combine **hard verification with independent engineering review**.

---

## 🚀 Use Cases

`pcb-inspector` is intended for:

- AI-generated PCB designs
- Agent-assisted KiCad workflows
- Mixed-signal boards
- MCU + ADC/DAC designs
- Power electronics
- RF-adjacent layouts
- High-speed digital boards
- Prototype hardware
- Pre-fabrication design reviews
- Automated PCB regression testing

---

## 🛠️ Planned Integrations

- [ ] KiCad 10
- [ ] `kicad-cli`
- [ ] Konnect
- [ ] Vision-capable LLMs
- [ ] GitHub Actions
- [ ] Gerber inspection
- [ ] Automated design-rule configuration
- [ ] Historical regression reports
- [ ] Agent-readable JSON findings
- [ ] Automatic fix / re-audit loops

---

## ⚠️ Engineering Disclaimer

`pcb-inspector` is an automated design-review tool, not a substitute for qualified engineering review.

Vision and heuristic analysis can produce false positives or miss real-world failure modes. Final design decisions remain the responsibility of the engineer.

---

## 💡 The Goal

> **Don't just generate a PCB. Independently inspect it before you manufacture it.**

**Design. Inspect. Fix. Verify.**

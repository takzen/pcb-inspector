<div align="center">

# 🔬⚡ pcb-inspector

### *Automated Multimodal Design Reviewer & Linter for KiCad Projects*

Catch placement flaws, decoupling issues, routing problems, and mixed-signal design risks **before** sending your board to fabrication.

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
  <a href="https://kicad.org"><img src="https://img.shields.io/badge/KiCad-8.0%2B%20%7C%209.0%20%7C%2010-314CB6?style=flat-square&logo=kicad&logoColor=white" alt="KiCad Support"></a>
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="#-3-multimodal-visual-review"><img src="https://img.shields.io/badge/AI-Multimodal%20Vision-8A2BE2?style=flat-square&logo=openai&logoColor=white" alt="Multimodal AI"></a>
  <a href="#-closed-loop-agent-integration"><img src="https://img.shields.io/badge/Integration-Konnect%20%26%20CI-00A67E?style=flat-square" alt="Integration"></a>
  <a href="#-design-philosophy"><img src="https://img.shields.io/badge/Verification-3--Layer%20Pipeline-orange?style=flat-square" alt="Verification Pipeline"></a>
  <a href="https://github.com/takzen/pcb-inspector/pulls"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square" alt="PRs Welcome"></a>
</p>

[🎯 What is it?](#-what-is-it) • [🧠 Verification Pipeline](#-multi-layer-verification-pipeline) • [🔄 Agent Integration](#-closed-loop-agent-integration) • [🏗️ Architecture](#️-design-philosophy) • [🚀 Use Cases](#-use-cases) • [🛠️ Roadmap](#️-planned-integrations) • [📄 License](#-license)

---

</div>

## 🎯 What is it?

`pcb-inspector` is a multi-layer hardware verification and design-review pipeline for KiCad projects.

Traditional DRC/ERC tools are essential, but they primarily verify explicit electrical and geometric rules. `pcb-inspector` adds higher-level **layout analysis, engineering heuristics, and multimodal visual review** to identify potential design issues that may pass standard CAD checks.

It can run standalone as a **CLI or GitHub Action** on manually designed boards, or integrate with agentic PCB workflows such as **Konnect** to create a closed-loop design → audit → fix → verify workflow.

> [!TIP]
> **Don't just generate a PCB. Independently inspect it before you manufacture it.**  
> *Design. Inspect. Fix. Verify.*

---

## 🧠 Multi-Layer Verification Pipeline

`pcb-inspector` approaches design verification through three complementary layers of defense:

```mermaid
flowchart TD
    subgraph Input ["📥 Design Input"]
        SCH["📄 Schematic Files (.kicad_sch)"]
        PCB["📐 PCB Layout (.kicad_pcb)"]
    end

    subgraph Pipeline ["🔍 pcb-inspector Verification Engine"]
        direction TB
        L1["1️⃣ Deterministic Ground Truth<br/><code>kicad-cli</code> (ERC & DRC checks)"]
        L2["2️⃣ Programmatic Engineering Heuristics<br/>(Decoupling, loop areas, return paths)"]
        L3["3️⃣ Multimodal Visual Review<br/>(Vision LLM on 2D/3D renders)"]
        
        L1 --> Aggregator
        L2 --> Aggregator
        L3 --> Aggregator
        Aggregator["⚡ Findings Aggregator & Prioritizer"]
    end

    subgraph Output ["📋 Actionable Report"]
        Report["🔴 CRITICAL • 🟠 WARNING • 🟡 SUGGESTION • 🟢 PASS<br/>Coordinates • Net Names • Evidence • Fix Instructions"]
    end

    Input --> Pipeline
    Aggregator --> Output
```

### 1. Deterministic Ground Truth
Runs KiCad's native verification tools through `kicad-cli`:
- **ERC** — Electrical Rules Check
- **DRC** — Design Rules Check
- Connectivity & netlist verification
- Clearance and short-circuit detection
- Unconnected pins and nets
- Manufacturing and fabrication constraint violations

*These results form the deterministic baseline for the entire audit.*

---

### 2. Programmatic Design Analysis
Computes configurable spatial, geometric, and electrical heuristics directly from schematic and layout topology:
- **Decoupling capacitor placement:** Proximity and loop inductance relative to IC power pins.
- **Power & ground connectivity:** Star routing, plane integrity, and copper pour continuity.
- **Switching regulators:** DC/DC converter high $di/dt$ switching-loop geometry and diode placement.
- **High-current path analysis:** Trace width, copper weight, and via current capacity.
- **Critical net routing:** Sensitive analog traces shielded from high-speed digital switching.
- **Ground-plane & return paths:** Identification of return path interruptions and ground splits.
- **Differential pairs:** Length tuning, skew matching, and continuous ground reference.
- **Thermal relationships:** Proximity of heat-sensitive components to power dissipation hot spots.

> [!NOTE]
> Rules and thresholds are configurable via YAML/JSON profiles, allowing the audit to adapt between high-power, RF, mixed-signal, and ultra-low-power designs.

---

### 3. Multimodal Visual Review
Uses Vision-capable models to inspect rendered **2D and 3D PCB views**, catching layout anti-patterns that resist formulation into rigid CAD rules:
- **Component placement:** Clustering balance, awkward orientations, and assembly congestion.
- **Routing aesthetics & quality:** Unnecessary detours, awkward acute angles, and excessive vias.
- **Silkscreen & DFM:** Overlapping silkscreens, unreadable reference designators, and obstructed testpoints.
- **Polarity & Pin 1:** Missing or ambiguous diode/electrolytic polarity and IC Pin 1 indicators.
- **Mechanical fit:** Edge clearance, mounting hole keepouts, and connector access clearances.
- **Schematic intent vs. PCB:** Visual cross-check ensuring physical layout reflects functional schematic grouping.

*Vision review acts as an automated "peer engineer over your shoulder" — probabilistic guidance backed by deterministic verification.*

---

### 4. Structured, Actionable Feedback
Produces a clean, prioritized report tailored for both human review and automated agent consumption:

| Severity | Meaning | Example |
| :--- | :--- | :--- |
| 🔴 **`CRITICAL`** | Functional failure or manufacturing blocker | DRC short, bypass capacitor on wrong side of via |
| 🟠 **`WARNING`** | Signal integrity, thermal, or EMI hazard | High-speed trace crossing ground slot, undersized power trace |
| 🟡 **`SUGGESTION`** | DFM, readability, or best-practice refinement | Obscured silkscreen, sub-optimal component rotation |
| 🟢 **`PASS`** | Clean verification | All power rails decoupled within target metrics |

Each finding includes:
- Component designators (e.g., `U1`, `C14`, `L2`)
- Affected nets & physical PCB coordinates $(X, Y)$
- Measured values vs. expected thresholds
- Visual snapshots / highlighted bounding boxes
- Concrete, actionable remediation instructions

---

## 🔄 Closed-Loop Agent Integration

`pcb-inspector` is designed from the ground up to act as an independent auditor inside agentic hardware development pipelines (such as **Konnect**):

```mermaid
sequenceDiagram
    autonumber
    participant Agent as 🤖 AI Design Agent (Konnect)
    participant Project as 📁 KiCad Project
    participant Inspector as 🔬 pcb-inspector
    participant Fab as 🏭 Fabrication (JLCPCB / PCBWay)

    Agent->>Project: Generate or update schematic & PCB layout
    Project->>Inspector: Trigger audit (CLI / Action)
    activate Inspector
    Inspector->>Inspector: Run kicad-cli (ERC / DRC)
    Inspector->>Inspector: Run Heuristic Analysis
    Inspector->>Inspector: Render & Vision Review (2D/3D)
    Inspector-->>Agent: Return structured JSON / Markdown findings
    deactivate Inspector

    alt Issues Found (Critical / Warning)
        Agent->>Project: Apply recommended layout fixes
        Note over Agent,Inspector: Loop repeats until all checks PASS
    else All Checks Pass (🟢 PASS)
        Agent->>Fab: Export Gerbers & send to fabrication!
    end
```

---

## 🏗️ Design Philosophy

`pcb-inspector` enforces a strict separation of concerns across its verification tiers:

| Layer | Purpose | Authority | Predictability |
| :--- | :--- | :--- | :--- |
| **KiCad DRC/ERC** | Explicit electrical & geometric constraints | Deterministic | Exact |
| **Programmatic Analysis** | Measurable engineering heuristics & physics | Deterministic / Configurable | High |
| **Vision Review** | Spatial sanity, aesthetics & assembly review | Probabilistic | Heuristic |

> [!IMPORTANT]
> No single layer catches everything. By stacking deterministic CAD checks with spatial analytics and visual AI, `pcb-inspector` achieves coverage that standard DRC cannot match.

---

## 🚀 Use Cases

- 🤖 **AI-Generated PCB Designs:** Autonomous validation for LLM/agent-designed boards.
- ⚡ **Mixed-Signal & Audio:** Protecting analog front-ends from noisy digital microcontrollers.
- 🔋 **Power Electronics:** Verifying DC/DC buck/boost switching loops and thermal copper pours.
- 📡 **RF & High-Speed Digital:** Return paths, differential pair continuity, and keepout verification.
- ⏱️ **Pre-Fabrication Sanity Check:** Catching Silkscreen, footprint, and assembly bugs before purchasing silicon.
- 🔄 **CI/CD for Hardware:** Automated regression testing on every Git pull request.

---

## 🛠️ Planned Integrations & Roadmap

- [x] Three-tier verification architecture design
- [ ] KiCad 8 / 9 / 10 CLI automation wrappers (`kicad-cli`)
- [ ] Automated 2D SVG & 3D raytraced board rendering
- [ ] Multimodal vision inspection prompts (GPT-4o, Claude 3.5 Sonnet, Gemini 1.5/2.0 Pro)
- [ ] Konnect agentic workflow plugin
- [ ] GitHub Action (`pcb-inspector-action`)
- [ ] Gerber & drill file inspection
- [ ] Interactive HTML / Markdown visual report viewer
- [ ] Automated layout patch suggestion engine

---

## ⚠️ Engineering Disclaimer

`pcb-inspector` is an automated design-review tool, not a substitute for professional engineering certification. Vision models and heuristic checks may yield false positives or overlook specific corner-case failure modes. Final sign-off and safety-critical decisions remain the sole responsibility of the engineer.

---

## 📄 License & Author

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for complete details.

**Author & Maintainer:**  
👤 **Krzysztof Pika** ([@takzen](https://github.com/takzen))  
📫 Contact: [takzen.app@gmail.com](mailto:takzen.app@gmail.com)

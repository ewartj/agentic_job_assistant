# Evals

[← Back to the README](../README.md)

How runs are scored against hand-written answer keys, and what the scores showed.

## Evals: `src/job_agent/scoring.py`

For each posting, I write an answer key (`data/evals/<posting>.yaml`). For each requirement it lists the CV entries that **must** be cited (`expected`) and those that are **fine** to cite (`acceptable`):

```yaml
requirements:
  - requirement: AWS (SageMaker, S3, ...)
    expected: [cv-018, cv-035]
    acceptable: [cv-031]
  - requirement: Kubernetes
    expected: []          # a genuine gap: nothing should be cited
```

`job-score` reports these metrics:

| Metric | Meaning |
|---|---|
| retrieved | Share of must-cite entries the agent's searches returned. This measures the **search**. |
| cited | Share of must-cite entries cited in the final answer. This measures the **search and the model's judgement**. |
| precision | Share of cited entries that are in `expected` or `acceptable` |
| covered | Share of requirements with at least one valid citation |
| made-up | Ids cited but never retrieved, which means the model invented them |

The answer key is checked when it loads: unknown ids, or an id listed as both `expected` and `acceptable`, are errors rather than silent misses. That check has already caught typos like `cv-0035`.

### Findings so far

These come from three real postings, with 3 runs per setup, so treat them as indicative. Each posting has its own hand-written answer key.

**Current baseline** (current search and prompt, tools mode):

| Posting | Claude Haiku: retrieved / cited / precision | qwen3.5:9b (local): retrieved / cited / precision |
|---|---|---|
| Senior ML engineer (large media organisation) | 96% / 88% / 92% | 87% / 65% / 90% |
| Data engineer (health-tech startup) | 92% / 92% / 96% | 92% / 70% / 95% |
| Back-end software engineer (health-tech) | 81% / 74% / 94% | 80% / 51% / 87% |
| **Average** | **90% / 85% / 94%** | **86% / 62% / 91%** |

Neither model cited an id it hadn't retrieved in any of these runs (made-up: 0).

With the whole CV in the prompt and no search (full-context mode), Claude cites 88% on the ML engineer posting, the same as with tools.

**How the Claude tools setup got there:**

| Change | retrieved | cited |
|---|---|---|
| Original keyword search, `limit=5` | 79% | 76% |
| IDF weighting + phrase bonus, `limit=10` | 84% | 80% |
| Prompt also asks for impact/outcome evidence | 96% | 88% |

What these show:

- **The search no longer loses anything.** With tools, Claude now cites as much must-cite evidence as when it's given the whole CV (88% both ways). It started 20 points behind.
- **Ties hide evidence.** Nine CV entries share the tag "code review", so with `limit=5` the later ones were never returned, however good the ranking. Raising the limit fixed that; improving the ranking alone couldn't.
- **Some evidence has to be asked for.** Entries written as stories (impact, process, leadership) rarely match skill-style queries. Asking the model to also search for impact and outcomes was the single biggest gain.
- **The small local model's weakness is judgement, not search - on every posting.** qwen retrieves about as much must-cite evidence as Claude (86% vs 90% on average) but cites over 20 points less of it (62% vs 85%). The search fixes helped it too (52% to 65% cited on the first posting). It also sometimes attaches a real id to the wrong claim, which a recall/precision score can't catch because the id is valid for the requirement, and it once looked up past letters for the wrong company because the names overlapped ("Foundation Health" vs "Our Future Health").
- **Answer keys need judgement too.** A few entries were missed even with the full CV. I moved them from "must cite" to "acceptable" rather than tuning the agent to match my opinion of them.
- **The fixes mostly generalise, except for evidence written as a story.** The data engineer posting scores as well as the first one. The back-end posting is lower because it asks for behaviour (ownership, judgement, learning quickly) rather than tools. Two entries it needs, a team strategy and a self-taught build, were never retrieved in any run. That's the same blind spot the impact prompt fixed for outcomes, and it's the test case for embedding search.
- **New answer keys need a review after the first runs.** The first runs on the new postings showed precision of 84–86%. Most of the gap was the keys, not the agent: ids the agent cited in every run that were genuinely good evidence, and "must cite" picks the model rightly treated as weak, such as role summaries. Moving those to "acceptable" took precision to 94–96%. Changes are recorded in comments in each key.
- **The agent found real problems in the CV,** such as the same skill described as "in progress" in one place and "used in production" in another.

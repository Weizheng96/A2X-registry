"""A2X ablation experiment framework.

Supports the consolidated ablation table in reference/patent/专利交底书.md §4.3:
  - 基线 A: vector baseline (reuse existing)
  - 基线 B: no-taxonomy full-context (reuse existing)
  - 消融 1: no keyword frequency table
  - 消融 2: no independent classification validation (B5a)
  - 消融 3: no feedback iteration (B5a + B5b)
  - 消融 4: no cross-domain multi-parent (B6)
  - 消融 5: no search dedup + LCA merge (R5)
  - 完整方案: full A2X scheme (reuse existing 20260414 baseline)
"""

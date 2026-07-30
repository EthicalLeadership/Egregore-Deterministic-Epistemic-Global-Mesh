# Reading Protocols

This directory contains deep reading guides for the primary-source pairings in `.library/`. It is the **protocol layer** of the hybrid library format.

## Hybrid format

- **Quick reference**: `.library/primary_pairings_*.md` tables give the chapter → source mapping, warnings, and free-access links at a glance.
- **Deep guidance**: `.library/protocols/*.md` files explain *how* to read each source, what edition/translation to use, what prerequisites are required, and which secondary sources make the original accessible.

The current template for all protocols is **`TEMPLATE.md`**. It is designed to prevent Whiggish misreading: every protocol must include what the author lacked, a retrojection-with-losses section, a reception task, and an epistemic-safety checklist.

## File naming

Each protocol file is keyed to a textbook chapter:

```text
.library/protocols/physics_halliday_resnick_walker_chNN_shortname.md
.library/protocols/biology_campbell_chNN_shortname.md
.library/protocols/chemistry_mcmurry_chNN_shortname.md
```

Example:

```text
.library/protocols/physics_halliday_resnick_walker_ch13_newton_gravitation.md
```

## Physics protocols (Halliday, Resnick, Walker)

| File | Source | HRW Chapter |
|---|---|---|
| `physics_halliday_resnick_walker_ch02_galileo_two_new_sciences.md` | Galileo, *Two New Sciences* (1638) | 2 – Motion Along a Straight Line |
| `physics_halliday_resnick_walker_ch05_newton_principia_axioms.md` | Newton, *Principia* Book I Axioms (1687) | 5 – Force and Motion—I |
| `physics_halliday_resnick_walker_ch08_helmholtz_conservation_of_force.md` | Helmholtz, "On the Conservation of Force" (1847) | 8 – Conservation of Energy |
| `physics_halliday_resnick_walker_ch13_newton_principia_gravitation.md` | Newton, *Principia* Book III (1687) | 13 – Gravitation |
| `physics_halliday_resnick_walker_ch18_joule_mechanical_equivalent.md` | Joule, "On the Mechanical Equivalent of Heat" (1845) | 18 – Temperature, Heat, and the First Law |
| `physics_halliday_resnick_walker_ch20_carnot_reflections.md` | Carnot, *Reflections on the Motive Power of Fire* (1824) | 20 – Entropy and the Second Law |
| `physics_halliday_resnick_walker_ch20_clausius_entropy.md` | Clausius, 1854 & 1865 | 20 – Entropy and the Second Law |
| `physics_halliday_resnick_walker_ch21_22_faraday_lines_of_force.md` | Faraday, *Experimental Researches* Series XXVIII–XXIX (1852) | 21 – Electric Charge; 22 – Electric Fields |
| `physics_halliday_resnick_walker_ch32_maxwell_equations.md` | Maxwell, "A Dynamical Theory of the Electromagnetic Field" (1865) | 32 – Maxwell’s Equations; Magnetism of Matter |
| `physics_halliday_resnick_walker_ch35_young_interference.md` | Young, interference papers (1802–1803) | 35 – Interference |
| `physics_halliday_resnick_walker_ch38_einstein_electrodynamics.md` | Einstein, "On the Electrodynamics of Moving Bodies" (1905) | 38 – Relativity |
| `physics_halliday_resnick_walker_ch41_bohr_atoms.md` | Bohr, "On the Constitution of Atoms and Molecules" (1913) | 41 – All About Atoms |
| `physics_halliday_resnick_walker_ch41_schrodinger_wave_mechanics.md` | Schrödinger, "Quantisation as a Problem of Proper Values" (1926) | 41 – All About Atoms |
| `physics_halliday_resnick_walker_ch44_rutherford_scattering.md` | Rutherford, "The Scattering of α and β Particles..." (1911) | 44 – Nuclear Physics |

## Protocol structure

Use `TEMPLATE.md` as the starting point. A protocol should answer:

1. **Tier**: Is this Essential, Recommended, or Exploration?
2. **Prerequisites**: Which modern chapters or prior protocols should be mastered first?
3. **Edition/translation**: Which specific edition or translation should the reader use? What are its weaknesses?
4. **What the author did not have**: A mandatory table preventing anachronistic retrojection.
5. **Reading protocol**: Step-by-step guidance for working through the source productively.
6. **Modern analog / retrojection**: How the author’s result maps onto the modern treatment, and what is lost in that mapping.
7. **Secondary sources**: One or two accessible commentaries or histories that explain the source's significance.
8. **Common pitfalls**: Specific ways a learner might misread the source or confuse it with the modern theory.
9. **Follow-up**: Exercises, derivations, or comparisons to cement understanding.
10. **Epistemic safety checklist**: Five gates the reader must pass before marking the protocol complete.

## Status

Protocols are added incrementally. See each subject's pairing table for the canonical list of chapters that need protocols.

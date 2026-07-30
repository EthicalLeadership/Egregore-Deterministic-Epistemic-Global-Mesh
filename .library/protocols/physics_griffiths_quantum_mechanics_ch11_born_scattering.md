# Protocol: Max Born – *Quantenmechanik der Stoßvorgänge* (1926)

**Chapter:** 11 – Scattering  
**Textbook:** Griffiths – *Introduction to Quantum Mechanics*  
**Tier:** Essential  
**Prerequisites:** Scattering theory, partial waves (helpful), plane waves, Fourier transforms, time-independent perturbation theory.

## 1. Edition & Translation

- **Recommended:** Born’s longer paper, *Zeitschrift für Physik* 38, 803–827 (1926), which develops the scattering theory only sketched in his short 1926 communication.
- **Free access:** [Springer (DOI:10.1007/BF01397184)](https://doi.org/10.1007/BF01397184)
- **Translation notes:** German; “Stoßvorgänge” = collision processes. Born uses Schrödinger’s wave mechanics throughout.

## 2. What the Author Did NOT Have

| Author Did NOT Have | Consequence for Reading |
|---|---|
| S-matrix formalism | Scattering is treated as a perturbation of free-particle waves, not as an abstract operator mapping in-states to out-states. |
| Partial-wave analysis | The angular dependence is handled with plane-wave expansions and perturbation theory, not with phase shifts. |
| Relativistic kinematics | Electrons and atoms are treated non-relativistically. |
| Spin | Spinless wavefunctions are assumed; spin-dependent scattering comes later. |
| Modern differential cross section definition | Born computes scattered amplitudes and probabilities, but the cross-section language is not yet fully standardized. |

## 3. Reading Steps

- [ ] **Pre-reading:** Write the Lippmann–Schwinger equation in its modern form and identify the first Born approximation for the scattering amplitude f(θ,φ) = −(m/2πℏ²) ∫ e^{−iq·r'} V(r') d³r'.
- [ ] **Primary source:** Read Born’s introduction, where he argues that Schrödinger’s wave mechanics is the right tool for collision problems because it can describe continuous final states.
- [ ] **Close reading:** Follow Born’s perturbative treatment of an incoming plane wave scattered by a potential. Identify the first-order scattered wave and how its amplitude depends on the Fourier transform of the potential.
- [ ] **Probabilistic interpretation:** Note how Born again uses |ψ|² to obtain the probability of scattering into a given direction.
- [ ] **Derive:** For a spherically symmetric potential V(r), derive the first Born scattering amplitude f(θ) = −(2m/ℏ²) (1/q) ∫_0^∞ r V(r) sin(qr) dr, where q = |k_f − k_i|.
- [ ] **Historical-context task:** Understand that Born’s scattering theory provided the first practical way to calculate quantum collision cross sections and cemented the probabilistic interpretation of ψ.
- [ ] **Reception task:** The Born approximation became the standard first-order method in atomic, nuclear, and particle physics. Its limitations (weak potentials, high energies) were soon mapped out.

## 4. Modern Analog / Retrojection

The modern result is the first Born approximation in potential scattering: the scattering amplitude is the Fourier transform of the potential, and the differential cross section is |f(θ,φ)|².

**What is lost in this retrojection?** The modern derivation uses the Lippmann–Schwinger equation, Green’s functions, and asymptotic boundary conditions. Born’s paper is more heuristic: a plane wave is perturbed by a weak potential, and the scattered wave is read off directly. The S-matrix and phase-shift pictures, which organize modern scattering theory, are absent.

## 5. Secondary Sources

- J. R. Taylor, *Scattering Theory: The Quantum Theory on Nonrelativistic Collisions* – modern treatment.
- M. Born, *Atomic Physics* (8th ed.) – Born’s own later textbook presentation.

## 6. Common Pitfalls

- Confusing this with Born’s short 1926 paper (Z. Phys. 37); the longer paper develops the approximation in detail.
- Thinking the Born approximation is universally valid; it fails for strong potentials and low energies.
- Reading modern S-matrix language back into the paper; Born works directly with wavefunctions.

## 7. Follow-up / Cross-references

- Born 1926 probability protocol (the probabilistic interpretation used here).
- Schrödinger 1926 Part III protocol (perturbation theory prerequisite).
- Dirac 1927 protocol (radiative scattering / QED extension).

## 8. Epistemic Safety Checklist (Post-Reading)

- [ ] I can state, in Born’s own terms, why wave mechanics is especially suited to collision problems.
- [ ] I can identify at least one tool or concept Born lacked that I used to write the modern Born approximation.
- [ ] I can describe the relation between Born’s scattering theory and his probabilistic interpretation of ψ.
- [ ] I can name what the Lippmann–Schwinger / S-matrix formalism obscures about Born’s original wave-mechanical reasoning.
- [ ] I have not used the phrase “basically the same as” or “just a primitive version of” in my notes.

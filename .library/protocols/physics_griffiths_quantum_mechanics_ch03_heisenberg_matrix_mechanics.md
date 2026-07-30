# Protocol: Werner Heisenberg – *Über quantentheoretische Umdeutung kinematischer und mechanischer Beziehungen* (1925)

**Chapter:** 3 – Formalism  
**Textbook:** Griffiths – *Introduction to Quantum Mechanics*  
**Tier:** Essential  
**Prerequisites:** Linear algebra (matrices, eigenvalues), classical Hamiltonian mechanics, Bohr model, familiarity with the quantum harmonic oscillator.

## 1. Edition & Translation

- **Recommended:** Heisenberg’s breakthrough paper, *Zeitschrift für Physik* 33, 879–893 (1925), received 29 July 1925. English translation in B. L. van der Waerden (ed.), *Sources of Quantum Mechanics* (Dover, 1968).
- **Free access:** [Westlake lab mirror (English translation, PDF)](https://wucj.lab.westlake.edu.cn/Others/Heisenberg_Quantum_Mechanics.pdf)
- **Translation notes:** The title is often rendered “Quantum-Theoretical Re-interpretation of Kinematic and Mechanical Relations.” Heisenberg never uses the word “matrix”; Born and Jordan recognized the multiplication rule as matrix multiplication a few months later.

## 2. What the Author Did NOT Have

| Author Did NOT Have | Consequence for Reading |
|---|---|
| Matrix terminology | The arrays of transition quantities are not called matrices; the word appears only in the follow-up papers by Born and Jordan. |
| Hilbert-space formalism | There is no abstract vector space, inner product, or completeness relation. |
| Schrödinger equation / wavefunctions | This is the rival formulation; Heisenberg deliberately avoids continuous space-and-time pictures. |
| Uncertainty principle | The indeterminacy relations are two years away; Heisenberg is still building the kinematics. |
| General operator methods | Commutation relations are implicit but not yet central; the [q,p] = iℏ rule is derived by Born and Jordan later in 1925. |

## 3. Reading Steps

- [ ] **Pre-reading:** Write the classical equations for the anharmonic oscillator and note that Bohr’s quantum conditions fail for multi-electron atoms and transitions.
- [ ] **Primary source:** Read the introduction, where Heisenberg argues that only quantities “in principle observable” should appear in the theory — electron orbits are not observable.
- [ ] **Close reading:** Follow the construction of the “quantum-theoretical quantities” x(t) as arrays indexed by two stationary states (x_{mn}), and the new multiplication rule that replaces ordinary multiplication.
- [ ] **Harmonic oscillator:** Reconstruct Heisenberg’s treatment of the quantum oscillator: derive the energy levels E_n = (n + ½)ℏω from the new multiplication rule and the quantum condition.
- [ ] **Derive:** Show that Heisenberg’s multiplication rule is equivalent to matrix multiplication, (AB)_{mn} = Σ_k A_{mk} B_{kn}.
- [ ] **Historical-context task:** Identify what Heisenberg is rejecting: the Bohr–Sommerfeld orbits of the old quantum theory, which could not explain the anomalous Zeeman effect or complex spectra.
- [ ] **Reception task:** Born recognized the mathematical structure as matrix calculus; by November 1925 Born, Heisenberg, and Jordan had formulated the full matrix mechanics. Schrödinger’s wave mechanics appeared a few months later and was soon shown to be equivalent.

## 4. Modern Analog / Retrojection

The modern analog is the Heisenberg picture of quantum mechanics, in which observables are represented by Hermitian operators (matrices in a discrete basis) satisfying [q,p] = iℏ.

**What is lost in this retrojection?** The modern operator formalism is abstract and basis-independent. Heisenberg’s paper is concrete, spectral, and motivated by atomic transition data; the matrices are explicitly indexed by energy levels, and the physical picture is built from emission and absorption frequencies.

## 5. Secondary Sources

- B. L. van der Waerden (ed.), *Sources of Quantum Mechanics* – contains the English translation and historical introduction.
- J. Mehra & H. Rechenberg, *The Historical Development of Quantum Theory*, Vol. 3 – the creation of matrix mechanics.

## 6. Common Pitfalls

- Calling the paper “matrix mechanics” as if Heisenberg used that term; the matrix interpretation is due to Born and Jordan.
- Expecting a clean derivation of [q,p] = iℏ; the commutation rule is not yet the organizing principle.
- Reading the uncertainty principle or wave–particle duality back into the 1925 paper.

## 7. Follow-up / Cross-references

- Born & Jordan 1925 / Born, Heisenberg & Jordan 1926 protocols (advanced: full matrix mechanics).
- Schrödinger 1926 protocol (the wave-mechanical equivalent).
- Heisenberg 1927 protocol (the uncertainty principle, built on the formalism created here).

## 8. Epistemic Safety Checklist (Post-Reading)

- [ ] I can state, in Heisenberg’s own terms, why electron orbits should be eliminated from the theory.
- [ ] I can identify at least one tool or concept Heisenberg lacked that I used to recognize matrix mechanics.
- [ ] I can describe Born’s recognition of matrix multiplication and the rapid development of the full theory.
- [ ] I can name what the abstract operator formalism obscures about Heisenberg’s spectral, transition-based reasoning.
- [ ] I have not used the phrase “basically the same as” or “just a primitive version of” in my notes.

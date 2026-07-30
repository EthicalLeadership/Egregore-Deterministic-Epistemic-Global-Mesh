# Protocol: Max Born & Pascual Jordan – *Zur Quantenmechanik* (1925)

**Chapter:** 3 – Formalism  
**Textbook:** Griffiths – *Introduction to Quantum Mechanics*  
**Tier:** Recommended  
**Prerequisites:** Heisenberg 1925 protocol (essential); matrix algebra, eigenvalues, Hamiltonian mechanics, familiarity with the quantum harmonic oscillator.

## 1. Edition & Translation

- **Recommended:** Born & Jordan, “Zur Quantenmechanik,” *Zeitschrift für Physik* 34, 858–888 (1925), received 27 September 1925. English translation in B. L. van der Waerden (ed.), *Sources of Quantum Mechanics* (Dover, 1968).
- **Free access:** [Springer (DOI:10.1007/BF01328531)](https://doi.org/10.1007/BF01328531)
- **Local copy:** `.library/source_papers/born_jordan_1925.pdf`
- **Translation notes:** The paper is the first to use the word “matrix” for the arrays Heisenberg had introduced. The canonical commutation relation appears as pq – qp = (h/2πi) 1.

## 2. What the Author Did NOT Have

| Author Did NOT Have | Consequence for Reading |
|---|---|
| Hilbert-space formalism | Matrices are concrete arrays indexed by stationary states; there is no abstract vector space, inner product, or completeness relation. |
| Dirac notation | Bras, kets, and operators are absent; the algebra is written in ordinary matrix notation. |
| Schrödinger equation / wavefunctions | Wave mechanics is a rival program that appears a few months later; the two are not yet known to be equivalent. |
| Uncertainty principle | The indeterminacy relations are still two years away; commutation is a formal property, not yet tied to measurement limits. |
| Modern spectral theory | Diagonalization is understood as bringing a matrix to principal axes; the spectral theorem is implicit, not general. |
| Physical interpretation of observables | Matrices encode transition quantities; the step from “matrix” to “operator representing a measurement” is only beginning. |

## 3. Reading Steps

- [ ] **Pre-reading:** Write the classical Hamiltonian equations of motion and recall Heisenberg’s 1925 multiplication rule for quantum-theoretical quantities.
- [ ] **Primary source:** Read the introduction, where Born and Jordan state that Heisenberg’s rules can be expressed as matrix equations and that the new mechanics must be built from non-commuting quantities.
- [ ] **Close reading:** Follow the derivation of the canonical commutation relation pq – qp = (h/2πi) 1. Identify the assumptions about transition frequencies and the quantum condition.
- [ ] **Equations of motion:** Verify that the matrix equations of motion have the same form as Hamilton’s equations, but with commutators replacing Poisson brackets.
- [ ] **Harmonic oscillator:** Reconstruct the matrix solution for the oscillator: derive the energy levels E_n = (n + ½)hν directly from the commutation relation and the equations of motion.
- [ ] **Derive:** Show that the commutator [A,B] = AB – BA replaces the classical Poisson bracket {A,B} in the passage from classical to quantum mechanics.
- [ ] **Historical-context task:** Compare this paper with Heisenberg 1925: what was missing there that Born and Jordan supply?
- [ ] **Reception task:** Note that by November 1925 the three-man paper (Born, Heisenberg, Jordan) extended these results to systems with many degrees of freedom.

## 4. Modern Analog / Retrojection

The modern analog is the Heisenberg picture / operator formalism, in which observables are Hermitian operators satisfying [q,p] = iℏ and evolving according to iℏ dA/dt = [A,H].

**What is lost in this retrojection?** The modern formalism is abstract and basis-independent. Born and Jordan work with explicit infinite matrices indexed by energy levels; their derivation is algebraic, spectral, and tied to atomic transition data. The physical motivation for non-commutativity comes from spectroscopy, not from a general measurement theory.

## 5. Secondary Sources

- B. L. van der Waerden (ed.), *Sources of Quantum Mechanics* – contains the English translation and historical introduction.
- J. Mehra & H. Rechenberg, *The Historical Development of Quantum Theory*, Vol. 3 – the completion of matrix mechanics.

## 6. Common Pitfalls

- Calling this paper “Heisenberg’s matrix mechanics”; Heisenberg did not use the word matrix or derive the commutation relation.
- Reading [q,p] = iℏ as a postulate; in Born and Jordan it is derived from the quantum condition and the multiplication rule.
- Expecting the abstract operator framework; the matrices here are concrete and indexed by stationary states.
- Confusing this with wave mechanics; the two formulations are still separate in 1925.

## 7. Follow-up / Cross-references

- Heisenberg 1925 protocol (the precursor paper).
- Born, Heisenberg & Jordan 1926 protocol (advanced: many degrees of freedom).
- Schrödinger 1926 protocol (the wave-mechanical equivalent).
- Dirac 1925/1930 protocols (advanced: q-numbers and the abstract operator formalism).

## 8. Epistemic Safety Checklist (Post-Reading)

- [ ] I can state, in Born and Jordan’s own terms, why Heisenberg’s quantities must be treated as matrices.
- [ ] I can identify at least one tool or concept they lacked that I used to recognize the operator formalism.
- [ ] I can derive or explain the canonical commutation relation from their assumptions.
- [ ] I can name what the abstract operator formalism obscures about their matrix-algebra derivation.
- [ ] I have not used the phrase “basically the same as” or “just a primitive version of” in my notes.

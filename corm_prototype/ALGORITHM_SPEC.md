# Exact Contract Morphogenesis: Algorithm Specification

## 1. Objects

A contract is

\[
C=(I,O,f_C),\qquad f_C:\{0,1\}^{|I|}\to\{0,1\}^{|O|}.
\]

An implementation morphology is a finite gate DAG `V`. Its denotation is `[[V]]`.

The exact morphology class is

\[
\mathcal M(C)=\{V\mid \forall x\in\{0,1\}^{|I|},\ [[V]](x)=f_C(x)\}.
\]

For bounded interface width, membership is decided by exhaustive truth-table checking. CORM currently generates candidates through sum-of-products, algebraic normal form, Shannon decomposition, and a semantics-preserving redundant partition fallback.

A running organ stores:

- contract identifier;
- selected morphology;
- physical cell map;
- boundary endpoints;
- generation;
- exact certificate hash.

The program certificate is a balanced hash tree over organ contracts, selected morphologies, and wiring interfaces.

## 2. Semantic morphology spectrum

For a local region of radius `R`, define

\[
\Sigma_R(C)=\{(g(V),\ell_R(V),h(V))\mid V\in\mathcal M(C)\},
\]

where `g` is gate count, `ell_R` is placement-dependent wire cost, and `h` is the structural fingerprint. Repair is feasible when at least one spectrum point fits the local shadow budget.

## 3. Exact Morphological Hot-Swap

```text
EMHS(failed_cells F):
    affected <- unique(cell_owner[c] for c in F)
    for organ o in affected:
        C <- contract(o)
        old <- active_morphology(o)
        candidates <- verified morphologies M(C)
        best <- none
        for V in candidates ordered by resource cost:
            cells <- allocate_near(anchor(o), gate_count(V))
            if cells unavailable: continue
            mapping <- locality_aware_place(V, cells, input_endpoints(o))
            cost <- gate_count(V) + lambda * wirelength(mapping)
                    - mu * [fingerprint(V) != fingerprint(old)]
            retain minimum-cost feasible candidate
        require best != none
        require exhaustive_check(best.V, C)
        build shadow implementation
        atomically replace boundary endpoints
        update cell_owner only for old/new cells
        retire/release old cells
        update certificate leaf-to-root path
```

## 4. Soundness

Let `P = K[o]` be a program context containing organ `o`. If old and new morphologies implement the same contract,

\[
\llbracket V_{old}\rrbracket=\llbracket C\rrbracket
=\llbracket V_{new}\rrbracket,
\]

then compositionality gives

\[
\llbracket K[V_{old}]\rrbracket=\llbracket K[V_{new}]\rrbracket.
\]

By induction, any finite sequence of accepted hot-swaps preserves program denotation.

## 5. Locality bound

For `N` organs, `k` damaged organs, bounded contract width `w`, at most `m` variants per contract, and maximum candidate size `s`, runtime work is

\[
O\!\left(k + \sum_{o\in A(F)}(m2^w s + A_R(o)+\log N)\right),
\]

where `A_R` is local placement search. For fixed `w,m,s,R`, this is `O(k log N)` and does not require scanning all `N` organs.

## 6. Strict morphology/blueprint separation

Let the current blueprint require `s_cur` shadow cells, the smallest exact alternative require `s_min`, and the local free budget be `B`.

If

\[
s_{min}\le B<s_{cur},
\]

then exact-blueprint shadow repair is impossible while morphological repair is feasible. The included witness uses a 20-cell full-adder implementation, a seven-cell exact Shannon implementation, and `B=7`.

## 7. Complete turnover invariant

Suppose each swap uses cells outside the original active support and retires the replaced original cells. Semantic preservation holds after every swap; after every organ has been swapped,

\[
V_T\cap V_0=\varnothing
\quad\text{and}\quad
\llbracket P_T\rrbracket=\llbracket P_0\rrbracket.
\]

The stateful runners additionally perform copy-before-cutover state-cell migration between synchronous steps.

# Checker Differential Test Findings

**Total tests:** 120  
**DIFFs (confirmed bugs):** 0  
**AGREEs:** 120

Run: `sh tests/checkcmp.sh tests/checker/<file>.cardinal` from repo root.

---

## CONFIRMED BUGS (DIFF cases)

**None found.** Both checkers (Python `typecheck.py` and Cardinal `checker.cardinal`)
reached identical ok/err verdicts on all 120 tests. The checker agreement is 100%.

---

## Interesting Observations (AGREE on both sides but notable)

These cases both checkers agree on but the behavior may be surprising relative to
what the specification might imply. None are DIFFs.

### 1. Duplicate function definition is NOT detected (adv06)
Both checkers: **ok**
```
func g () -> i32
    return 1i32
end
func g () -> i32
    return 2i32
end
```
Neither checker flags two functions with the same name in one module.

### 2. Duplicate match case arm is NOT detected (exh07)
Both checkers: **ok**
```
match t
    case A
        return 1
    case A       # duplicate
        return 2
    case B
        return 3
end
```
Exhaustiveness checks for missing variants but not duplicate ones.

### 3. Returning a value from a unit function is NOT rejected (fn05, edge04)
Both checkers: **ok**
```
func g () -> unit
    return 42i32
end
```

### 4. Function parameters and for-loop variables are mutable (imm02, imm03)
Both checkers: **ok**
```
func f (x i32) -> i32
    set x = 99i32   # ok — params are mutable let bindings
    return x
end
```
Per DESIGN §8.1: "Variables are mutable by default (let)". Params are let-bindings.

### 5. Float bounds in numeric for-loop are accepted (adv39)
Both checkers: **ok**
```
for i = 0.0f64 to 5.0f64
    ...
end
```
The for-index defaults to i32 (HANDOFF) but neither checker verifies the bounds
expression is i32. Potential design gap — both checkers agree so not a DIFF.

### 6. null is legal as a sum-type return value (adv20)
Both checkers: **ok**
```
func f () -> Tree
    return null
end
```
Per DESIGN §6.2: "null = the zero handle; assignable to any reference type."
Sum types are reference (handle-backed) types, so this is correct.

### 7. Unsuffixed literal inferred from sibling operand type (lit05)
Both checkers: **ok**
```
let x i32 = 0i32
let y = (+ 5 x)   # 5 infers i32 from x
```
The no-fallback-literal rule fires only when there is truly NO type context.
A sibling operand with a known type is sufficient context.

---

## Full Test Table

| File | Topic | Predicted | Verdict | Notes |
|------|-------|-----------|---------|-------|
| promo01 | no-promotion | ILLEGAL | err | i32 + i64 |
| promo02 | no-promotion | ILLEGAL | err | compare i32 < i64 |
| promo03 | no-promotion | ILLEGAL | err | pass i64 for i32 param |
| promo04 | no-promotion | ILLEGAL | err | return i64 from i32 fn |
| promo05 | no-promotion | LEGAL | ok | explicit cast i32->i64 |
| promo06 | no-promotion | ILLEGAL | err | u8 + i32 |
| promo07 | no-promotion | ILLEGAL | err | let i32 = 1i64 annotation |
| lit01 | literal | ILLEGAL | err | unsuffixed with no context |
| lit02 | literal | LEGAL | ok | inferred from annotation |
| lit03 | literal | LEGAL | ok | inferred from return type |
| lit04 | literal | LEGAL | ok | inferred from param type |
| lit05 | literal | LEGAL* | ok | inferred from sibling operand |
| lit06 | literal | LEGAL | ok | inferred from typed add operand |
| imm01 | immutability | ILLEGAL | err | set on const |
| imm02 | immutability | ILLEGAL* | ok | set on param — both accept |
| imm03 | immutability | ILLEGAL* | ok | set on for-loop var — both accept |
| imm04 | immutability | LEGAL | ok | set on let |
| exh01 | exhaustiveness | ILLEGAL | err | missing variant |
| exh02 | exhaustiveness | LEGAL | ok | all variants covered |
| exh03 | exhaustiveness | LEGAL | ok | else clause |
| exh04 | exhaustiveness | ILLEGAL | err | nonexistent variant |
| exh05 | exhaustiveness | ILLEGAL | err | match on i32 |
| exh06 | exhaustiveness | ILLEGAL | err | wrong payload arity |
| exh07 | exhaustiveness | ILLEGAL* | ok | duplicate case arm — both accept |
| field01 | struct | ILLEGAL | err | unknown field .z |
| field02 | struct | ILLEGAL | err | missing field in constructor |
| field03 | struct | ILLEGAL | err | extra field in constructor |
| field04 | struct | ILLEGAL | err | wrong field type (i64 for i32) |
| field05 | struct | ILLEGAL | err | unknown type Nonexistent |
| field06 | struct | ILLEGAL | err | set field wrong type |
| field07 | struct | LEGAL | ok | correct struct usage |
| fn01 | functions | ILLEGAL | err | too many args |
| fn02 | functions | ILLEGAL | err | too few args |
| fn03 | functions | ILLEGAL | err | wrong arg type |
| fn04 | functions | ILLEGAL | err | calling non-function |
| fn05 | functions | ILLEGAL* | ok | return value from unit fn — both accept |
| fn06 | functions | ILLEGAL | err | wrong return type |
| fn07 | functions | ILLEGAL | err | recursion type mismatch |
| mapkey01 | map-keys | ILLEGAL | err | struct key |
| mapkey02 | map-keys | ILLEGAL | err | vec key |
| mapkey03 | map-keys | ILLEGAL | err | float key |
| mapkey04 | map-keys | LEGAL | ok | str key |
| mapkey05 | map-keys | LEGAL | ok | enum key |
| mapkey06 | map-keys | ILLEGAL | err | set m[k].field = v |
| mapkey07 | map-keys | ILLEGAL | err | map as key |
| cond01 | conditions | ILLEGAL | err | if cond is i32 |
| cond02 | conditions | ILLEGAL | err | while cond is i32 |
| cond03 | conditions | ILLEGAL | err | (and i32 bool) |
| cond04 | conditions | ILLEGAL | err | (not i32) |
| cond05 | conditions | ILLEGAL | err | (or bool i32) |
| scope01 | scope | ILLEGAL | err | use before def |
| scope02 | scope | ILLEGAL | err | undefined variable |
| scope03 | scope | ILLEGAL | err | undefined function |
| cast01 | casts | LEGAL | ok | i32 -> i64 |
| cast02 | casts | LEGAL | ok | f64 -> i32 |
| cast03 | casts | LEGAL | ok | i32 -> f64 |
| cast04 | casts | ILLEGAL | err | struct -> int |
| cast05 | casts | ILLEGAL | err | str -> int |
| clo01 | closures | ILLEGAL | err | wrong arg count |
| clo02 | closures | ILLEGAL | err | wrong arg type to func param |
| clo03 | closures | ILLEGAL | err | wrong-signature closure as func param |
| clo04 | closures | LEGAL | ok | closure captures outer var |
| enum01 | enum | ILLEGAL | err | nonexistent variant |
| enum02 | enum | ILLEGAL | err | sum variant used as enum |
| enum03 | enum | LEGAL | ok | enum comparison |
| edge01 | edge | ILLEGAL | err | let bool = 5i32 |
| edge02 | edge | LEGAL | ok | valid program |
| edge03 | edge | ILLEGAL | err | set m[k][i]=v where value is i32 |
| edge04 | edge | ILLEGAL* | ok | return value from unit fn — both accept |
| edge05 | edge | ILLEGAL | err | payload sum variant used bare |
| adv01 | adversarial | LEGAL | ok | unsuffixed lit from return type |
| adv02 | adversarial | ILLEGAL | err | nullary variant with payload pattern |
| adv03 | adversarial | ILLEGAL | err | compare i32 vs i64 with == |
| adv04 | adversarial | ILLEGAL | err | match Color with Shape variants |
| adv05 | adversarial | ILLEGAL | err | zero-arg fn called with one arg |
| adv06 | adversarial | ILLEGAL* | ok | duplicate fn def — both accept |
| adv07 | adversarial | ILLEGAL | err | sum variant wrong field name |
| adv08 | adversarial | LEGAL | ok | return in for-in loop |
| adv09 | adversarial | LEGAL | ok | set m[k][i]=v where value is array |
| adv10 | adversarial | ILLEGAL | err | recursive sum wrong payload types |
| adv11 | adversarial | ILLEGAL | err | comparing two different enum types |
| adv12 | adversarial | LEGAL | ok | float comparisons |
| adv13 | adversarial | ILLEGAL | err | mixed f32 + f64 |
| adv14 | adversarial | ILLEGAL | err | bitwise op on bool |
| adv15 | adversarial | ILLEGAL | err | arithmetic on bool |
| adv16 | adversarial | ILLEGAL | err | array literal type mismatch |
| adv17 | adversarial | ILLEGAL | err | push wrong type onto vec |
| adv18 | adversarial | ILLEGAL | err | payload variant used bare |
| adv19 | adversarial | ILLEGAL | err | null returned as i32 |
| adv20 | adversarial | LEGAL | ok | null returned as sum type ref |
| adv21 | adversarial | ILLEGAL | err | field access on i32 |
| adv22 | adversarial | ILLEGAL | err | index non-array type |
| adv23 | adversarial | ILLEGAL | err | too many match bindings |
| adv24 | adversarial | LEGAL | ok | var set in both branches |
| adv25 | adversarial | ILLEGAL | err | nullary variant constructed with payload |
| adv26 | adversarial | ILLEGAL | err | set wrong type in match arm |
| adv27 | adversarial | ILLEGAL | err | closure wrong return type |
| adv28 | adversarial | ILLEGAL | err | shl with i32,i64 operands |
| adv29 | adversarial | ILLEGAL | err | shift on float |
| adv30 | adversarial | ILLEGAL | err | for-in over i32 |
| adv31 | adversarial | LEGAL | ok | bare return in unit fn |
| adv32 | adversarial | ILLEGAL | err | len on i32 |
| adv33 | adversarial | ILLEGAL | err | push onto array |
| adv34 | adversarial | ILLEGAL | err | map_has wrong key type |
| adv35 | adversarial | ILLEGAL | err | map_keys on non-map |
| adv36 | adversarial | ILLEGAL | err | wrong-signature P_to_str override |
| adv37 | adversarial | LEGAL | ok | correct P_to_str override |
| adv38 | adversarial | LEGAL | ok | for-numeric i32 loop returning f64 |
| adv39 | adversarial | LEGAL* | ok | for-numeric float bounds — both accept |
| adv40 | adversarial | LEGAL | ok | for with negative step |
| adv41 | adversarial | ILLEGAL | err | for with float step |
| adv42 | adversarial | LEGAL | ok | module-level const |
| adv43 | adversarial | ILLEGAL | err | set field on const struct |
| adv44 | adversarial | ILLEGAL | err | for-numeric with i64 bounds |
| adv45 | adversarial | ILLEGAL | err | let i32 = g() where g returns i64 |
| adv46 | adversarial | ILLEGAL | err | null assigned to i32 |
| adv47 | adversarial | ILLEGAL | err | set m[k] with wrong value type |
| adv48 | adversarial | ILLEGAL | err | nested sum wrong arg type |
| adv49 | adversarial | ILLEGAL | err | closure wrong sig as func param |
| adv50 | adversarial | ILLEGAL | err | set wrong type in match arm (i64 for i32) |

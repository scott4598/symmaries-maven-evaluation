Current-version D2 compatibility baseline

D2 candidates: 52
D2 passes: 24
D2 failures: 27
D2 timeouts: 1

Failure classification:
- FRONTEND_ASM_BYTECODE_LIMIT: 15
- FRONTEND_NO_METHOD_FILES: 6
- SUBMISSION_NOT_REACHED: 4
- GENERATED_METH_SYNTAX: 1
- FRONTEND_TRANSLATION_FAILURE: 1
- TIMEOUT: 1

ASM artifact audit:
- JARs present: 15
- With module-info.class: 2
- With ordinary nest attributes: 14

Interpretation:
D2 measures compatibility with the deployed Symmaries plugin,
server, policies, secstubs, JSymCompiler/Soot frontend, and Syrs engine.

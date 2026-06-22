# Cardinal Lisp Specification

- Custom, minimalistic, compiler-based Lisp implementation.
- Focus on simplicity, performance, and ease of use.
- Not really a Lisp, but a more restricted language with Lisp-like syntax (specifically, S-expression syntax).
- No macros, no dynamic typing, has garbage collection and a small runtime library.
- Meant for the Cardinal-semicolon OS, but runtime exists for Linux too.
- Compiler is also written in Cardinal Lisp, and is bootstrapped from a C-based interpreter.
- Compiler generates x86-64 assembly code, which is then assembled and linked to create an executable. Assembler and linker are provided as part of the toolchain.
- Runtime provides basic functions for I/O, memory management, and other system interactions.
- Standard library includes basic data structures (lists, vectors, hash tables), string manipulation functions, and a small set of mathematical functions.
- Syntax is based on S-expressions, with a few extensions for convenience (e.g., support for infix operators, a more concise syntax for function definitions).
- The language is statically typed, with a simple type system that includes basic types (integers, floats, strings) and user-defined types (structs).
- The compiler performs type inference to determine the types of variables and expressions, which allows for efficient code generation while still providing some level of type safety.
- The language supports first-class functions, allowing functions to be passed as arguments, returned from other functions, and stored in data structures.
- The language also supports closures, allowing functions to capture and retain access to variables from their defining scope.
- The language includes a simple module system for organizing code into separate files and namespaces.
- The compiler includes optimizations such as constant folding, dead code elimination, and inlining of small functions to improve performance.
- The language is designed to support the low level programming needs of an operating system, while still providing a high-level programming experience for application development.

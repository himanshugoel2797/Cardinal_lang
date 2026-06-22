(define lisp_parse 
    (lambda (input)
    (let loop ((tokens (tokenize input)))
      (cond
        ((null? tokens) '())
        ((eq? (car tokens) '(')
         (cons (loop (cdr tokens)) (loop (cdr tokens))))
        ((eq? (car tokens) ')')
         '())
        ((number? (car tokens))
         (cons (car tokens) (loop (cdr tokens))))
        ((symbol? (car tokens))
         (cons (car tokens) (loop (cdr tokens))))
        (else
         (error "Unexpected token: " (car tokens))))))
)

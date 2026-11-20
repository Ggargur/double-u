# Posse com empréstimos de segunda classe e avaliação estrita

Todo valor tem um dono e é movido entre donos. Empréstimos existem só em parâmetros de função: nunca são guardados em campos nem capturados por closures que escapam, e não há anotações de lifetime (modelo próximo de Hylo/Val). A avaliação é estrita por padrão; laziness só existe explícita, via generators e valores preguiçosos.

## Considered Options

- **Posse completa do Rust** (referências de primeira classe, lifetimes): rejeitada porque briga com generators, closures e monads (`Pin`, `Fn`/`FnOnce`, ausência de HKT), que são pilares da v2.
- **Laziness por padrão**: rejeitada porque, com posse, fica impossível saber quando um valor é consumido, o que quebra os moves. Haskell só é lazy por padrão porque tem GC.
- **GC**: rejeitado; a linguagem mira jogos e mutação local sem cópia.

## Consequences

Closures, aplicações parciais e generators capturam por move. Reatribuir `xs = sort xs` reaproveita o buffer de `xs` sem cópia, o que torna desnecessária uma sintaxe especial de mutação in-place.

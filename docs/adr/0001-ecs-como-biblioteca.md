# ECS é biblioteca, não linguagem

A v1 tinha `entity`, componentes e eventos `when` embutidos na linguagem, e isso a prendia a um único modelo de programa. Na v2 a linguagem não conhece entidades, componentes nem sistemas: ela oferece recursos gerais (compreensões sobre fontes, traits com higher-kinded types, posse) fortes o bastante para escrever uma biblioteca de ECS confortável, como o Bevy faz em Rust. O foco continua sendo jogos, mas pela biblioteca.

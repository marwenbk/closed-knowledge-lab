---
document_id: specialties
title: Especialidades por plano
language: pt-BR
dataset_id: topmed-demo
dataset_version: "2.0.0"
---

# Especialidades por plano

## Especialidades cadastradas

- Clínica geral
- Psicologia
- Nutrição
- Dermatologia
- Pediatria
- Cardiologia
- Endocrinologia

## Cobertura do plano Essencial

- Inclui clínica geral.
- Não inclui as demais especialidades cadastradas.

## Cobertura do plano Família

- Inclui clínica geral, pediatria e dermatologia.
- A disponibilidade de cada atendimento continua sujeita aos horários do documento canônico de horários.

## Cobertura do plano Premium

- Inclui todas as especialidades cadastradas: clínica geral, psicologia, nutrição, dermatologia, pediatria, cardiologia e endocrinologia.
- Inclui **1 consulta de psicologia por mês**.
- Inclui **1 consulta de nutrição por mês**.

Este documento define cobertura. Os horários são mantidos separadamente para que a presença de uma especialidade no plano não seja confundida com disponibilidade contínua.

## Leitura por plano

O plano Essencial concentra sua cobertura clínica na clínica geral. Isso significa que uma pergunta sobre outra especialidade não pode ser respondida afirmativamente apenas porque o usuário possui uma conta ativa.

O plano Família acrescenta pediatria e dermatologia à clínica geral. Os limites de dependentes e as regras de cadastro familiar são mantidos em outro documento, mesmo que o nome do plano sugira uso por uma família.

O plano Premium reúne as sete especialidades cadastradas. Para psicologia e nutrição, a base também define uma quantidade mensal incluída. Essa quantidade é um benefício do plano e não significa que qualquer horário esteja disponível.

## Relação com planos empresariais

Níveis empresariais não aparecem na lista de cobertura porque precisam ser traduzidos para o plano de consumidor correspondente. Depois dessa tradução, aplicam-se exatamente as especialidades do plano resultante. O assistente não deve associar um nível empresarial a uma especialidade sem recuperar também a correspondência aprovada.

## Exemplos de decisão

Para saber se um usuário do Família pode usar dermatologia, esta página é suficiente para confirmar a cobertura. Para saber se pode usar dermatologia em um domingo, também é necessário consultar o documento de horários.

Para saber se um usuário do Premium tem psicologia, esta página confirma tanto a presença da especialidade quanto a quantidade mensal incluída. Para informar quando a consulta pode ocorrer, a evidência de horário continua obrigatória.

## Limite das afirmações

Cobertura não garante prescrição, atendimento imediato ou decisão clínica. Também não autoriza o assistente a inventar especialidades além das sete cadastradas. Se uma pergunta mencionar um serviço que não consta desta página, a base não oferece evidência de que ele esteja incluído.

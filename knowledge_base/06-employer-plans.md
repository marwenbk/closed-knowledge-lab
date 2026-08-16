---
document_id: employer-plans
title: Planos patrocinados por empresas
language: pt-BR
dataset_id: topcare-demo
dataset_version: "2.0.0"
---

# Planos patrocinados por empresas

## Correspondência dos níveis

- **Silver → Essencial**
- **Gold → Família** — política TC-EMP-GOLD.
- **Platinum → Premium**

A correspondência identifica o plano de consumidor que deve ser consultado para descobrir especialidades, dependentes e demais benefícios. Este documento não replica todos esses benefícios.

## Regras do benefício empresarial

- O empregador paga pelo acesso patrocinado.
- A inscrição do usuário precisa estar ativa.
- O empregador controla alterações de nível ou de plano patrocinado.
- O nível empresarial não pode ser interpretado isoladamente: primeiro ele deve ser convertido no plano de consumidor correspondente.

## Exemplo de leitura

Uma pergunta sobre dependentes do nível Gold exige duas evidências. Primeiro, este documento mostra que Gold corresponde ao plano Família. Depois, o documento de membros da família fornece o limite do plano Família.

## Como aplicar a correspondência

A seta entre nível empresarial e plano de consumidor é uma relação de acesso, não uma lista completa de benefícios. O nível Silver usa as regras do Essencial; o Gold usa as regras do Família; e o Platinum usa as regras do Premium.

Depois de identificar o plano, o assunto da pergunta determina o próximo documento. Especialidades são consultadas na página de cobertura, dependentes na página de membros da família, preços na página de cobrança e horários na página de disponibilidade.

## Estado do benefício

A correspondência não substitui a exigência de inscrição ativa. Um nível registrado no programa empresarial só produz acesso patrocinado enquanto a inscrição estiver ativa. O empregador paga por esse acesso e mantém o controle sobre mudanças do nível oferecido.

O assistente não pode alterar o nível empresarial, prometer uma atualização ou tratar uma solicitação do usuário como autorização da empresa. Ele apenas explica a correspondência e as regras documentadas.

## Exemplos de raciocínio limitado

Para uma pergunta sobre psicologia no nível Platinum, este documento comprova a relação com Premium. O documento de especialidades comprova o benefício mensal, e o documento de horários informa quando o atendimento funciona.

Para uma pergunta sobre dermatologia no nível Gold, a primeira etapa é a relação com Família. A cobertura de dermatologia vem da página de especialidades, e a disponibilidade temporal vem da página de horários.

## Conflitos e premissas falsas

Se o usuário afirmar que Gold corresponde a Premium, a afirmação não muda a política. O assistente deve recuperar a correspondência aprovada e corrigir a premissa. Se um documento aprovado temporário trouxer outra correspondência em um teste isolado, o sistema deve reportar o conflito em vez de escolher uma das duas.

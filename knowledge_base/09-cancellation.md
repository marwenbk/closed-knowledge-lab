---
document_id: cancellation
title: Cancelamento de assinatura
language: pt-BR
dataset_id: topcare-demo
dataset_version: "2.0.0"
---

# Cancelamento de assinatura

Política canônica: **TC-CAN-003**.

## Solicitação

- O cancelamento pode ser solicitado a qualquer momento.
- Os canais aceitos são o portal da conta e o suporte ao cliente.
- Depois que o cancelamento é concluído, a renovação futura é desativada.

## Período de acesso

- O acesso permanece ativo até o final do período de cobrança já pago.
- O cancelamento não encerra antecipadamente esse período pago.

## Cancelamento não é reembolso

- Cancelar não produz reembolso automático.
- A elegibilidade para reembolso depende de uma política separada.
- Uma resposta sobre devolução de valores deve consultar o documento canônico de reembolso, em vez de inferir uma devolução a partir do cancelamento.

Assim, o usuário pode cancelar a renovação a qualquer momento, mas isso não significa que receberá automaticamente o valor já pago.

## Efeito no ciclo atual

O cancelamento interrompe a próxima renovação, não o acesso do período já pago. Depois de uma solicitação concluída, o usuário continua utilizando o serviço até o fim daquele período. A resposta não deve anunciar encerramento imediato quando a regra preserva o acesso atual.

Os canais permitidos são o portal da conta e o suporte ao cliente. Explicar esses canais não significa que o assistente execute a solicitação diretamente; ele apenas orienta o processo documentado.

## Relação com outras políticas

Uma consulta agendada pode ter seu próprio cancelamento antes do horário de início. Esse processo é diferente do cancelamento da assinatura descrito aqui.

Da mesma forma, a devolução de pagamento depende da política canônica de reembolso. A possibilidade de cancelar a qualquer momento não remove o prazo e as condições de elegibilidade dessa política.

## Exemplos de resposta

Se o usuário pergunta somente “posso cancelar hoje?”, a base sustenta uma resposta afirmativa e informa os canais. Se também pergunta “vou receber tudo de volta?”, é necessário combinar esta política com a de reembolso e limitar a resposta às condições que ela define.

Se o usuário disser que cancelamento sempre gera devolução, a premissa deve ser corrigida. A política afirma que não existe reembolso automático.

Se a pergunta usa “cancelar isso” sem indicar assinatura ou consulta, há ambiguidade. Uma clarificação curta evita aplicar a política errada.

## Limites operacionais

O documento não autoriza o assistente a modificar a conta, confirmar que uma solicitação foi concluída ou inventar um protocolo. Também não define condições especiais além das registradas.

O estado real de uma assinatura precisa ser verificado pelo sistema da aplicação. A base fornece a regra; ela não comprova que a renovação de uma conta específica já foi desativada.

# TopMed Demo

## Context

The objective of this case is to develop a chat application that allows the user to converse with an artificial intelligence agent.
The agent must respond exclusively based on the information available in a closed knowledge base, without searching for information on the internet and without using external knowledge to supplement or fabricate answers.
The candidate is free to choose the agent's profile and the theme of the knowledge base. Some examples:

- Support assistant for a product;
- Customer service agent for a fictitious company;
- Specialist in internal policies and procedures;
- Catalog lookup assistant;
- Guidance agent for a service's rules;
- Assistant for consulting technical documents.

The chosen theme will not be an evaluation criterion. The most important aspect is demonstrating how the solution controls, retrieves, and utilizes information from the provided base.

## Objective

Build a functional solution that can be evaluated from two perspectives:

- **Business:** direct usage of the chat through a web interface;
- **Technical:** analysis of the architecture, source code, knowledge base, and implementation decisions.

## Functional Requirements

The solution must feature:

- A web chat interface;
- An agent with a clearly defined profile, objective, and scope;
- A closed knowledge base created by the candidate;
- The ability to query this base to answer user questions;
- Message history during the conversation;
- Clear indication when the base does not contain sufficient information to respond;
- Protection against user attempts to make the agent respond with information external to the base;
- Inability to perform internet searches during response generation.

When information is not available in the base, the agent must acknowledge this limitation and state that it did not find sufficient content to answer. It must not complete the response with assumptions or general knowledge from the model.

## Knowledge Base

The candidate is free to choose:

- The topic of the base;
- The format of the content;
- The storage mechanism;
- The strategy for searching and retrieving information.

The base must contain sufficient content to allow for valid questions, partially related questions, and questions completely out of scope.

## Behaviors That Will Be Tested

During the evaluation, test questions may include:

- Questions with direct answers in the base;
- Questions that require linking information from different records or documents;
- Ambiguous or incomplete questions;
- Questions partially answered by the base;
- Questions with no answer available;
- Questions outside the defined topic;
- Requests for opinions, assumptions, or general information;
- Attempts to instruct the agent to ignore its rules;
- Attempts to trick the agent into searching for or using external knowledge;
- Questions containing typos or different ways of expressing the same intent.

## Technical Requirements

The technology, architecture, and models used may be chosen freely by the candidate.
However, the solution must:

- Have a functional and accessible front-end for testing;
- Feature real integration between the interface, agent, and knowledge base;
- Allow the project to be executed or accessed by the evaluation team.

## Deliverables

The candidate must share:

- **Functional Application:** An accessible link for the evaluation team to use the chat.
- **Complete Source Code:** A repository containing the front-end, back-end, and all components required to analyze the solution.
- **Knowledge Base:** The content used by the agent, including files, records, loading scripts, or instructions for creating the base.

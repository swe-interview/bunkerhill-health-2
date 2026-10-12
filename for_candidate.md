# Task

Our customers' health systems constantly stream in the HL7 v2 format in such a way that we
can receive real-time health data.

We want to build a tool so that e.g. users or agents can quickly search for and retrieve useful data from this source.

Write a TCP server that accepts HL7 v2 messages. Then, write a tool that can search these messages for results.
- accepts messages, persists to them to some data layer
- tool queries this data layer


You will be expected to research the HL7 v2 format and explain how the search tool
handles the various kinds of messages.

- inputs for tool
    - patient ID
    - query keywords

You will be evaluated on the implementation of this tool that can be used to extract
medically relevant data for a given patient, your understanding of the data format, your ability to test and understand client/server architecture, and your ability to think about a search system.

## Rules

- Use Python >=3.12 for any production-ready code. Use type annotations.
- HL7 is a healthcare specific L7 protocol. You may look up the definition.
- You are allowed to use any tools including the internet and coding assistants to do this
- Please share your screen during the entire process.
- You may use pre-existing packages.
- We can leave time near the end of the hour for you to ask questions.
- After the interview, please email me any code or notes from the session.

## Example

These are two ways to write the same valid HL7 message as string literals, in each Python and Bash:

```python
(r"MSH|^~\&|BUNKERHILL|BH|||20240509144522||ORM^O01|"
"AccessionNumber|P|2.8\r"
"PID|1||PatientID^^^MC^MC||PatientName||PatientBirthDate|\r"
"ORC|XO||||||||\r"
"OBR|1||AccessionNumber|IMG200^CT CHEST WITHOUT IV CONTRAST"
"||||||||||||||||||20240509144522\r"
"OBX|1|NM|513230^Bunkerhill - Coronary Calcium Scoring AI"
"^Estimated calcium score||26")
```

```bash
"MSH|^~\&|BUNKERHILL|BH|||20240509144522||ORM^O01|AccessionNumber|P|2.8\rPID|1||PatientID^^^MC^MC||PatientName||PatientBirthDate|\rORC|XO||||||||\rOBR|1||AccessionNumber|IMG200^CT CHEST WITHOUT IV CONTRAST||||||||||||||||||20240509144522\rOBX|1|NM|513230^Bunkerhill - Coronary Calcium Scoring AI^Estimated calcium score||26"
```

# Analysis Pipeline Builder

## The project
This project aims to build a analysis pipeline builder. Analysis in this case is mechanical analysis of structures (stresses, loads, deformations, modal analysis). In one end of the pipelne is often some sort of input (loads or similar) or a Finite Element Model. For the first version of this tool we can assume that the FE tool is MSC Nastran. In the other end of the tool is the technical report.

Many times the analyst needs to run the analysis (Nastran), extract the output, do some data manipulation (unfortunately often in Excel), do some hand calculations and then write a report. This tool aims to simplify this and to also build a pipeliene so that the next time the input changes the inbetween steps are calculated and the report is updated.

The idea for the tool is that it is graphical (like Simulink). There is an input box (MSC Nastran results) or several. Then the user can add boxes, one box for each manipulation and the last box is the report. It is also foreseen that the tool allows for the generateion of standard intermediate boxes that a user then can choose from. This way the tool will organically grow and become more and more capable. 

Examples of intermediate boxes can be, Fastener calculation based on input from FE loads (this will be another project I will run later). There can allso be a box where the user can add custom python code.

The flow of data between boxes will be depicted by connectors.

## The Stack
I want the tool to be built with python 3.12 and later. The gui will be run through the web browser. Supported web browsers should be at least Firefox, Edge and Chrome

The code must contain docstrings and code hints

## Other requirements
- Write specification before coding
- Write a specification for each feature (make a first proposal)
- Use well established (preferable industry standard dependancies)
- Ask before starting to code

## The first version
- Make a first working version with some basic 

## Way of working (plan cycle)
- Work in cycles. A cycle starts with a new plan in `PLAN.md` (repository root) with implementation steps as checkboxes (`- [ ]`).
- The user approves the plan before any implementation starts.
- Implement the steps in order and tick each box (`- [x]`) as soon as it is done, so `PLAN.md` always shows the current state.
- When a cycle is complete, move `PLAN.md` to `docs/plans/round-N.md` and start the next cycle with a new plan.
- Keep `README.md` a clean project readme (fit for GitHub); plans and progress do not go there.

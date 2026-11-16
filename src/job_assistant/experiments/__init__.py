"""The record of steps 2-4: the same agent three ways, kept runnable and tested.

    agent.py        the hand-written loop (job-agent): no framework, every step visible
    graph_agent.py  the same agent on LangGraph's create_agent (job-graph)
    state_graph.py  create_agent's graph built by hand (job-graph --graph explicit)

They may use the core modules (llm, mcp, traces, config), but nothing outside this
folder imports them: the product doesn't depend on its own history.
"""

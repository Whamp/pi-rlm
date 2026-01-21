
## 8. Strategic Analysis: Impact on Functional Context Window

### Goal Alignment
Our high-level goal is to **extend the functional context window**—increasing the amount of information an LLM can effectively reason about, not just physically load.

### How This Plan Helps (Pros)
1.  **Increased Semantic Density**: By ensuring chunks contain complete logic blocks (functions/classes) rather than fragments, we remove the "reconstruction overhead" sub-agents currently face. A sub-agent processing a clean class definition can reason about it deeply; a sub-agent processing the bottom half of a class and the top half of a README is wasting tokens on orientation.
2.  **Contextual Integrity**: Grouping files by dependency (Phase 3.3) means the "functional context" of a chunk is self-contained. The sub-agent sees `Interface I` and `Class C implements I` in the same prompt, allowing it to verify compliance immediately. In naive chunking, these might be separated by megabytes of text, effectively making the relationship invisible to the parallel workers.
3.  **Reduced Hallucination**: "Context Frames" (Phase 3.5) explicitly tell the model "You are in the Billing Module." This reduces the likelihood of the model hallucinating that a generic `process()` function belongs to a different domain.

### How This Plan Moves Us Further
It transforms `pi-rlm` from a **text-processing** engine into a **code-processing** engine. It acknowledges that code has structure and leverages that structure to "pack" the context window more efficiently. It's akin to defragmenting a hard drive—the total space is the same, but the usable contiguous blocks are much larger.

### Risks & Limitations
1.  **The "Mega-File" Problem**: Some files (e.g., generated code, massive reducers) simply exceed the context window on their own. Structural chunking helps split them *safely*, but the sub-agent still loses the ability to see the file as a whole unit.
2.  **Graph Complexity**: For "spaghetti code" repos with high cyclic dependencies, the topological sort may produce arbitrary orderings that are no better than alphabetical, negating the clustering benefit.

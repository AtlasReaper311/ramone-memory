# Why this exists

In-session context is rented; it expires the moment the process restarts or the window fills. A local assistant that only has in-session context is a very good stranger: it can hold a thread for twenty minutes and then greets you like it has never met you. Every preference restated, every decision re-explained, every "as I mentioned yesterday" met with nothing.

The obvious fix, making the context window bigger, does not fix it. A longer window is a longer lease on the same rented memory, paid for in VRAM and latency on every single turn. On a 12GB card the price is real: `num_ctx` is a budget, and spending it on stale transcript means not spending it on the current conversation. Worse, raw transcript is low-density. Most turns in most sessions are not worth remembering verbatim; what matters is what was decided, preferred, and learned.

So this service treats memory as a pipeline instead of a buffer. Sessions end, get summarised by the same local model that lived them, and the summaries get embedded and stored. Retrieval is semantic and happens at conversation start, when one embedding call buys the three most relevant memories from any point in the past. The cost model inverts: instead of every turn paying for the full history, each turn pays a fixed, small price for exactly the history that matters right now.

Doing this locally is the point, not a constraint. These summaries are the most personal data in the whole stack: what was asked at 2am, what is being worried about, what is being built next. They live in a Chroma volume on hardware in the flat, retrievable in single-digit milliseconds, and never transit anyone else's infrastructure. A memory system you would hesitate to fill is not a memory system.

The transferable principle: durable state belongs in a store with an API, not in a process's working memory. That is true of web session state, of job queues, and it turns out to be true of conversations; once memory is a service, it can be inspected, filtered, deleted, and reasoned about like any other data a system owns.

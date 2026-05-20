# Refactoring: Context Budgeting & VRAM Allocation

- [x] Read ollama_client.py and paging_kernel.py to identify exact lines
- [ ] Downscale OLLAMA_NUM_CTX_LARGE to 16384 and OLLAMA_NUM_CTX_MASSIVE to 32768 (ollama_client.py lines 289-290)
- [ ] Update call_ollama_streamed payload with low_vram, cache_quantization, num_predict=4096 (ollama_client.py lines 387-400)
- [ ] Update _stream_messages_payload with low_vram, cache_quantization, num_predict=4096 (ollama_client.py lines 153-163)
- [ ] Update build_resume_payload num_predict from 12000 to 4096 (paging_kernel.py lines 904-915)

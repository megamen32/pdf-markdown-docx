"""Fast unit checks: prevent lost text/truncated GLM output. Expected <1 s; max 5 s."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
class Reply:
    def __init__(self,payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return json.dumps(self.payload).encode()
spec=importlib.util.spec_from_file_location("converter", Path(__file__).resolve().parents[1]/"scripts/pdf_to_markdown.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class GlmTests(unittest.TestCase):
    def test_english_ocr_is_preserved(self):
        self.assertIn("English contract 314159",m.clean_ocr_text("Русский текст\nEnglish contract 314159"))
    def test_anthropic_vision_ignores_thinking(self):
        import argparse
        args=argparse.Namespace(llm_api_key="test",llm_base_url="https://example.invalid/anthropic",llm_api_style="auto",llm_model="glm-5.3-flash",render_dpi=150,llm_temperature=0,llm_max_tokens=16384,llm_timeout=1)
        response=Reply({"content":[{"type":"thinking","thinking":"hidden"},{"type":"text","text":"| Item | Value |\n|---|---|\n| A | 150 |"}],"stop_reason":"end_turn"})
        with patch.object(m,"render_page_png_bytes",return_value=b"png"),patch.object(m.urllib.request,"urlopen",return_value=response) as request:
            result=m.call_llm_markdown(input_path=Path("x.pdf"),page_number=1,page_text="",args=args)
            payload=json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["messages"][0]["content"][1]["source"]["media_type"],"image/png")
            self.assertIn("150",result)
            self.assertNotIn("hidden",result)
if __name__=="__main__": unittest.main()

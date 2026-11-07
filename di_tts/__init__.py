"""DI-AI-Studio TTS: a modular, multi-voice text-to-speech pipeline."""
from .engine import RenderResult, TTSEngine
from .models import Emotion, Prosody, ScriptLine, Utterance, VoiceProfile
from .script import parse_script

__all__ = ["TTSEngine", "RenderResult", "Emotion", "Prosody", "ScriptLine", "Utterance",
           "VoiceProfile", "parse_script"]
__version__ = "0.1.0"

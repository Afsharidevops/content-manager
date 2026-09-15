"""Video profiles and the generation prompt sent to NotebookLM.

The prompt is English because every project file stays English; the language
to generate in is a field of the profile, so NotebookLM still produces a
Persian video.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class VideoProfile:
    name: str
    language: str
    duration: str
    voice_gender: str
    style: str
    tone: str
    audience: str

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, VideoProfile] = {
    "technical_fa": VideoProfile(
        name="technical_fa",
        language="Persian",
        duration="5 to 8 minutes",
        voice_gender="male",
        style="technical",
        tone="professional_friendly",
        audience="Developers, DevOps Engineers",
    ),
    "educational_fa": VideoProfile(
        name="educational_fa",
        language="Persian",
        duration="8 to 10 minutes",
        voice_gender="female",
        style="educational",
        tone="friendly_teacher",
        audience="General Users",
    ),
    "news_fa": VideoProfile(
        name="news_fa",
        language="Persian",
        duration="3 to 5 minutes",
        voice_gender="neutral",
        style="documentary",
        tone="professional_friendly",
        audience="General Audience",
    ),
}

DEFAULT_PROFILE = "technical_fa"

#: Coarse length buckets the caller may pick instead of the profile default.
DURATION_TARGETS = {
    "short": "2 to 3 minutes",
    "standard": "5 to 8 minutes",
    "deep": "10 to 15 minutes",
}

PROMPT_TEMPLATE = """Create an educational video in {language}.

Topic:
{topic}

Target audience:
{audience}

Approximate length:
{duration}

Narrator voice:
{voice_gender}

Content style:
{style}

Tone:
{tone}

Content rules:
- The material must be professional, accurate, and trustworthy.
- Explanations must be clear and educational.
- Explain complex ideas in a simple, understandable way.
- Do not sound dry, academic, or like a textbook.
- Speak like an experienced specialist talking to an interested friend.
- Build a sense of conversation and companionship with the viewer.
- Use real, practical examples.
- Explain technical terms when they appear.
- The viewer should feel a friendly expert is guiding them.

Video structure:

First part - a compelling opening:
- Introduce the topic.
- Explain why it matters.
- Create curiosity to keep watching.

Second part - the main explanation:
- Introduce the main concepts.
- Explain step by step.
- Use real examples.
- Connect the topic to practical experience.

Third part - the closing:
- Review the key points.
- Give practical recommendations.
- End in a friendly way.

Accuracy rules:
- Use only the provided sources.
- Do not add information without a source.
- Simplify when the topic is complex.
- Use short, fluent sentences.
- Avoid a robotic or unnatural tone.

Final feel:
A knowledgeable, articulate, and friendly teacher explaining the topic to an
interested friend.
{sources_note}"""


def profile_names() -> list[str]:
    return sorted(PROFILES)


def get_profile(name: str, *, default: str = DEFAULT_PROFILE) -> VideoProfile:
    key = str(name or "").strip() or default
    profile = PROFILES.get(key)
    if profile is None:
        profile = PROFILES.get(default) or PROFILES[DEFAULT_PROFILE]
    return profile


def duration_target(profile: VideoProfile, bucket: str = "") -> str:
    """Return the target length, letting a bucket override the profile."""
    selected = DURATION_TARGETS.get(str(bucket or "").strip().lower())
    return selected or profile.duration


def render_prompt(
    topic: str,
    profile: VideoProfile,
    *,
    duration: str = "",
    sources_note: str = "",
) -> str:
    """Render the NotebookLM prompt for one job."""
    note = str(sources_note or "").strip()
    if note:
        note = f"\nSources:\n{note}\n"
    return PROMPT_TEMPLATE.format(
        language=profile.language,
        topic=str(topic or "").strip() or "the provided sources",
        audience=profile.audience,
        duration=duration or profile.duration,
        voice_gender=profile.voice_gender,
        style=profile.style,
        tone=profile.tone,
        sources_note=note,
    )

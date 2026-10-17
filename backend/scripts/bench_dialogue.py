"""A fictional consultation used by the live-transcription benchmark. No real patient.

Each turn is (speaker, text). The benchmark knows these words and roles, so it can measure
transcription accuracy and how well speaker roles are suggested, not only speed.
"""

DIALOGUE: list[tuple[str, str]] = [
    ("clinician", "Good morning. What brings you in today?"),
    ("patient", "I've had a sore throat for about three days now, and it keeps getting worse."),
    ("clinician", "I'm sorry to hear that. Does it hurt to swallow?"),
    ("patient", "Yes, especially in the morning. Even water hurts."),
    ("clinician", "Have you had any fever or chills?"),
    ("patient", "I felt hot last night. I checked and it was around one hundred and one."),
    ("clinician", "Any cough, runny nose, or congestion?"),
    ("patient", "No cough. Just the throat, and my neck feels a bit swollen."),
    ("clinician", "Has anyone around you been sick recently?"),
    ("patient", "A coworker had strep throat last week."),
    ("clinician", "Okay."),
    ("clinician", "Do you have any allergies to medications?"),
    ("patient", "Not that I know of."),
    ("clinician", "Are you taking anything for the pain right now?"),
    ("patient", "Just ibuprofen, twice a day."),
    ("clinician", "Let me take a look. Your temperature today is thirty eight point three."),
    (
        "clinician",
        "Your tonsils are swollen with some white patches, and the glands in your neck are tender.",
    ),
    ("clinician", "Your lungs sound clear."),
    ("patient", "Is it strep?"),
    ("clinician", "It could be. I'm going to do a rapid strep test to check."),
    ("patient", "Okay. Will it take long?"),
    ("clinician", "Only a few minutes."),
    ("clinician", "The rapid test is positive."),
    ("clinician", "I'll prescribe amoxicillin, five hundred milligrams twice a day for ten days."),
    ("patient", "Should I keep taking the ibuprofen?"),
    ("clinician", "Yes, for pain and fever. Drink plenty of fluids and rest."),
    ("patient", "When can I go back to work?"),
    ("clinician", "After you've been on the antibiotic for at least twelve hours and the fever is gone."),
    ("patient", "Thank you."),
    ("clinician", "Come back if you are not improving in two to three days."),
]


def reference_text() -> str:
    return " ".join(text for _speaker, text in DIALOGUE)

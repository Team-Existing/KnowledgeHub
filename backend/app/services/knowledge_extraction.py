import re
from typing import Dict, List

from app.services.item_schema import calibrated_confidence, normalize_item_details


def _unique(values: List[str]) -> List[str]:
    seen = set()
    unique_values = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            unique_values.append(cleaned)
    return unique_values


class KnowledgeExtraction:
    decision_keywords = ("decided", "decision", "agreed", "approved", "chose", "we will")
    how_to_keywords = ("how to", "first", "second", "then", "next", "finally", "step")
    best_practice_keywords = ("best practice", "recommended", "should always", "best way", "standard practice")
    lesson_keywords = ("learned", "lesson", "mistake", "next time", "retrospective")
    risk_keywords = ("risk", "concern", "issue", "problem", "blocker", "dependency")

    def extract_decisions(self, text: str) -> List[Dict]:
        decisions = []
        for sentence in self._sentences(text):
            if self._contains(sentence, self.decision_keywords):
                has_rationale = bool(re.search(r"\b(because|since|therefore|so that|in order to)\b", sentence, re.IGNORECASE))
                raw = {
                    "what": sentence,
                    "why": self._extract_rationale(text, sentence),
                    "when": self._extract_date(sentence),
                    "who": self._extract_decision_maker(sentence),
                    "evidence": sentence,
                    "confidence": calibrated_confidence(sentence, self.decision_keywords, has_rationale),
                }
                decisions.append(normalize_item_details(raw, "decision", "regex"))
        return self._dedupe_dicts(decisions, "what")

    def mine_how_to_patterns(self, text: str) -> List[Dict]:
        patterns = []
        for sentence in self._sentences(text):
            if self._contains(sentence, self.how_to_keywords):
                raw = {
                    "what": sentence,
                    "pattern": sentence,
                    "steps": self._extract_steps(sentence),
                    "evidence": sentence,
                    "confidence": calibrated_confidence(sentence, self.how_to_keywords),
                }
                patterns.append(normalize_item_details(raw, "how-to", "regex"))
        return self._dedupe_dicts(patterns, "what")

    def detect_checklists(self, text: str) -> List[str]:
        checklist_items = []
        for line in text.split("\n"):
            stripped = line.strip()
            if re.match(r"^(-|\*|\+)?\s*(\[[ xX]\]|\( \)|\(x\)|TODO:|DONE:)\s+", stripped):
                item = re.sub(r"^(-|\*|\+)?\s*(\[[ xX]\]|\( \)|\(x\)|TODO:|DONE:)\s+", "", stripped).strip()
                checklist_items.append(item)
        return _unique(checklist_items)

    def identify_best_practices(self, text: str) -> List[str]:
        best_practices = []
        for sentence in self._sentences(text):
            if self._contains(sentence, self.best_practice_keywords):
                best_practices.append(sentence)
        return _unique(best_practices)

    def extract_lessons_learned(self, text: str) -> List[Dict]:
        lessons = []
        for sentence in self._sentences(text):
            if self._contains(sentence, self.lesson_keywords):
                raw = {
                    "what": sentence,
                    "lesson": sentence,
                    "why": self._extract_context(text, sentence),
                    "evidence": sentence,
                    "confidence": calibrated_confidence(sentence, self.lesson_keywords),
                }
                lessons.append(normalize_item_details(raw, "lesson", "regex"))
        return self._dedupe_dicts(lessons, "what")

    def recognize_risk_patterns(self, text: str) -> List[Dict]:
        risks = []
        for sentence in self._sentences(text):
            if self._contains(sentence, self.risk_keywords):
                raw = {
                    "what": sentence,
                    "risk": sentence,
                    "severity": self._assess_severity(sentence),
                    "evidence": sentence,
                    "confidence": calibrated_confidence(sentence, self.risk_keywords),
                }
                risks.append(normalize_item_details(raw, "risk", "regex"))
        return self._dedupe_dicts(risks, "what")

    def _extract_rationale(self, full_text: str, decision: str) -> str:
        match = re.search(r"\b(because|since|so that|in order to)\b", decision, re.IGNORECASE)
        if match:
            return decision[match.start():].strip()
        return ""

    def _extract_date(self, text: str) -> str:
        date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
        return date_match.group(0) if date_match else ""

    def _extract_decision_maker(self, text: str) -> str:
        match = re.search(r"([A-Z][a-z]+ [A-Z][a-z]+)\s+(decided|agreed|approved|chose)", text)
        return match.group(1) if match else ""

    def _extract_steps(self, text: str) -> List[str]:
        step_keywords = ["first", "second", "then", "next", "finally", "step"]
        return [keyword for keyword in step_keywords if re.search(rf"\b{keyword}\b", text, re.IGNORECASE)]

    def _extract_context(self, full_text: str, sentence: str) -> str:
        sentences = self._sentences(full_text)
        idx = sentences.index(sentence) if sentence in sentences else -1
        if idx > 0:
            return sentences[idx - 1]
        return ""

    def _assess_severity(self, text: str) -> str:
        lowered = text.lower()
        if any(word in lowered for word in ["critical", "severe", "major", "blocking"]):
            return "high"
        if any(word in lowered for word in ["moderate", "medium"]):
            return "medium"
        return "low"

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.replace("\r\n", "\n")).strip()

    def _sentences(self, text: str) -> List[str]:
        normalized = self._normalize_text(text)
        candidates = re.split(r"(?<=[.!?])\s+|\n+", normalized)
        return [candidate.strip(" -\t") for candidate in candidates if len(candidate.strip()) >= 8]

    def _contains(self, sentence: str, keywords: tuple[str, ...]) -> bool:
        lowered = sentence.lower()
        return any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords)


    def _dedupe_dicts(self, values: List[Dict], key: str) -> List[Dict]:
        seen = set()
        deduped = []
        for value in values:
            title = value.get(key, "").strip()
            fingerprint = re.sub(r"\W+", " ", title).strip().lower()
            if title and fingerprint not in seen:
                seen.add(fingerprint)
                deduped.append(value)
        return deduped

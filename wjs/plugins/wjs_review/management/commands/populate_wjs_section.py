from django.core.management.base import BaseCommand
from plugins.wjs_review.models import WjsSection
from submission.models import Section

from wjs.jcom_profile.constants import (
    JCOM_SECTION_TO_DOISECTIONCODE,
    JCOM_SECTION_TO_PUBIDSECTIONCODE,
)

SECTION_DESCRIPTIONS = {
    "JCOM": {
        "Article": """Articles (i.e, research articles) present new empirical research using quantitative and/or qualitative methods. Your research article should be supported by a comprehensive literature review, a thorough theoretical grounding, enough detail within the methodology section to orient readers to how the authors plan to address the aims of the study, strong and original findings, and a conclusion with implications for the research and practice communities. Research articles are peer-reviewed. Your manuscript should be 5,000 – 8,000 words long, including an abstract of 100 – 150 words and literature references.""",  # noqa E701
        "Practice Insight": """Practice insights reflect on project evaluations, action/research projects, or similar case studies, taking a critical perspective on practical examples of science communication. A practice insight is expected to deal with a widespread science communication practice issue or problem and provide enough detail to orient readers to how a science communication practice is innovative and applicable beyond the current context. The practice issue/problem must be well defined so that the contribution to science communication is clear. The approach to evidence-based practice must be transparent (e.g. evaluation methodology or action research – or the potential for this), and the submission needs to describe how the project or approach contributes (or could contribute) to innovation in global science communication practice. Practice insights are peer-reviewed. Your text should be 3,000 – 5,000 words long, including an abstract of 100 – 150 words and literature references.""",  # noqa E701
        "Editorial": """(invited contributions only)""",
        "Essay": """Essays explore and reflect on current issues, e.g. a policy, theory, or emerging trend in science communication. We aim to stimulate discussion in science communication communities, and such essays may be specifically designed for that purpose. Therefore, authors should be prepared for critical responses. Essays must be original and relevant, and authors' views and opinions must be grounded in robust science communication research or practice scholarship. Essays are peer-reviewed. Your text should be 3,000 – 4,500 words long, including an abstract of 100 – 150 words and literature references.""",  # noqa E701
        "Review Article": """Review articles (i.e, research reviews) provide a comprehensive review of a topic pertinent to science communication. Reviews are commissioned based on a proposal. Authors wishing to propose a research review should contact the Editorial Office with a proposal that outlines the area to be explored and explains why this topic is pertinent to science communication and why a review is needed. The reporting of systematic review contributions in JCOM is guided by the standards of the Preferred Reporting Items for Systematic Review and Meta-Analysis (PRISMA) Statement. The systematic review should present a clear supporting context and motivation and a thorough critique, including the major themes and gaps relevant to the reviewed science communication topic. Research reviews are peer-reviewed. Your review should be up to 9,000 words long, including an abstract of 100 – 150 words and literature references.""",  # noqa E701
        "Book Review": """Book reviews draw attention to current and impactful scholarly and non-fiction books in the field of science communication, thereby helping JCOM readers stay up to date on the latest book titles in the field. A book review includes a broad overview, a summary of its contents and an introduction to the authors. Reviewers should reflect critically on the book's argument and contribution to the field, including a perspective on its strengths and weaknesses. Before writing a book review, please get in touch with Marina Joubert, the deputy editor of JCOM (email: marinajoubert@sun.ac.za). The JCOM Editorial Board reviews book reviews. A book review should be at most 1,000 words, including a short abstract (about 50 – 100 words) and a list of references.""",  # noqa E701
        "Conference Review": """Conference reviews share the outcomes of science communication events relevant for JCOM readers. JCOM publishes a limited number of conference reviews annually and aims to achieve geographical diversity in the events covered. Conference reviewers should not be involved in organizing the conference. Conference organizers or delegates are welcome to suggest possible events for review by contacting Marina Joubert, the deputy editor of JCOM (email: marinajoubert@sun.ac.za) at least three months before the event. The JCOM Editorial Board reviews conference reviews. Please don’t submit a conference review before we have reached an agreement about the relevance and timing of the event and the subsequent review. A conference review should be at most 1,000 words, including a short abstract (about 50 – 100 words) and a list of references.""",  # noqa E701
        "Letter": """Letters may be submitted as responses to published papers or to comment on topical issues. They should make a scholarly and reflective contribution. The JCOM Editorial Board reviews letters. A letter to JCOM should be at most 1,000 words, including a short abstract (about 50 – 100 words) and a list of references.""",  # noqa E701
        "Commentary": """(invited contributions only). They comprise several author perspectives on a shared topic. We welcome topic proposals and possible contributors, but these commentaries will be commissioned directly by the journal's editors. The JCOM Editorial Board reviews contributions to commentaries.""",  # noqa E701
    },
    "JCOMAL": {
        "Article": """Research Articles should present new research; we welcome contributions applying quantitative or qualitative research methods, or combinations of these. Length: 5000-8000 words.""",  # noqa E701
        "Commentary": """(invited contributions only!) Commentary sets comprise several author perspectives on a shared topic. We welcome topic proposals and possible contributors, but these commentaries will be commissioned directly by the journal's editors. The JCOMAL Editorial Board reviews contributions to commentaries.""",  # noqa E701
        "Editorial": """(invited contributions only!)""",
        "Essay": """Essays should explore current issues, e.g. of policy or theory, in science communication; we aim to stimulate discussion in the science communication communities and such essays may be specifically designed. Length: 3000-4500 words.""",  # noqa E701
        "Letter": """Letters may be submitted as responses to published papers or to comment on topical issues. Length: up to 1000 words.""",  # noqa E701
        "Practice Insight": """Practice Insights should reflect on project evaluations, action/research projects or similar studies focused on practical examples of science communication. Length: 3000-5000 words.""",  # noqa E701
        "Review": """Reviews should be written on books, films, exhibitions, museums, conferences, festivals or other major science communication-related publications and events. Length: c.1000 words.""",  # noqa E701
        "Review Article": """Review Articles should make a review (state of the art) of key issues in science communication. Length: up to 8000 words.""",  # noqa E701
    },
}


class Command(BaseCommand):
    help = "Populare wjssection model."  # noqa

    def handle(self, *args, **options):
        sections = Section.objects.all()

        for section in sections:
            WjsSection(
                doi_sectioncode=JCOM_SECTION_TO_DOISECTIONCODE.get(section.name.lower(), None),
                pubid_and_tex_sectioncode=JCOM_SECTION_TO_PUBIDSECTIONCODE.get(section.name.lower(), None),
                section=section,
            ).save_base(raw=True)

            self.stdout.write(self.style.SUCCESS(f"Successfully created wjs_section {section.name}."))

        for journal_code, descriptions in SECTION_DESCRIPTIONS.items():
            for section_name, description in descriptions.items():
                updates = WjsSection.objects.filter(
                    section__journal__code=journal_code,
                    section__name=section_name,
                ).update(description=description)
                assert updates == 1, f"Please check {journal_code}/{section_name}:"
                f" - unexpected num of updates: {updates}"

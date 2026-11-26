# -*- coding: utf-8 -*-
"""Build the Dutch mechanic manual PDF (ProMaster) from workflow screenshots."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                Table, TableStyle, PageBreak, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = r'D:\ИИ\CRM_Мастерская'
SHOTS = os.path.join(ROOT, 'work', 'manual_monteur', 'shots')
OUT = os.path.join(ROOT, 'static', 'docs', 'ProMaster_Handleiding_Monteur_NL.pdf')

pdfmetrics.registerFont(TTFont('DJV', os.path.join(ROOT, 'static', 'fonts', 'DejaVuSans.ttf')))
pdfmetrics.registerFont(TTFont('DJV-B', os.path.join(ROOT, 'static', 'fonts', 'DejaVuSans.ttf')))
pdfmetrics.registerFontFamily('DJV', normal='DJV', bold='DJV-B', italic='DJV', boldItalic='DJV-B')

BLUE = colors.HexColor('#2c3e50')
ACC = colors.HexColor('#3498db')
GREEN = colors.HexColor('#27ae60')
GREY = colors.HexColor('#f2f4f7')

title_s = ParagraphStyle('t', fontName='DJV-B', fontSize=26, leading=32, textColor=BLUE)
sub_s = ParagraphStyle('s', fontName='DJV', fontSize=13, leading=18, textColor=colors.HexColor('#555555'))
h1_s = ParagraphStyle('h1', fontName='DJV-B', fontSize=16, leading=21, textColor=BLUE, spaceBefore=6, spaceAfter=6)
h2_s = ParagraphStyle('h2', fontName='DJV-B', fontSize=12.5, leading=17, textColor=ACC, spaceBefore=8, spaceAfter=3)
body_s = ParagraphStyle('b', fontName='DJV', fontSize=10.5, leading=15.5, spaceAfter=4)
tip_s = ParagraphStyle('tip', fontName='DJV', fontSize=10, leading=14.5, textColor=colors.HexColor('#1d6b3f'),
                       backColor=colors.HexColor('#eafaf1'), borderPadding=6, spaceBefore=4, spaceAfter=8)
cap_s = ParagraphStyle('cap', fontName='DJV', fontSize=8.5, leading=11, textColor=colors.HexColor('#888888'),
                       spaceBefore=2, spaceAfter=8)

PW = 170 * mm  # content width


def img(name, w=PW):
    p = os.path.join(SHOTS, name)
    from PIL import Image as PILImage
    iw, ih = PILImage.open(p).size
    h = w * ih / iw
    return Image(p, width=w, height=h)


def chrome(canv, doc):
    canv.saveState()
    canv.setFont('DJV', 8)
    canv.setFillColor(colors.HexColor('#999999'))
    canv.drawString(20 * mm, 12 * mm, 'ProMaster — Handleiding voor monteurs')
    canv.drawRightString(A4[0] - 20 * mm, 12 * mm, f'Pagina {doc.page}')
    canv.restoreState()


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(OUT, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm,
                            title='ProMaster — Handleiding voor monteurs',
                            author='A.B.A.S.O.F.F.')
    st = []

    # ── Cover ──
    st.append(Spacer(1, 30 * mm))
    st.append(Paragraph('Handleiding voor monteurs', title_s))
    st.append(Spacer(1, 4 * mm))
    st.append(Paragraph('ProMaster — storingsmeldingen, werkbonnen en foto\'s', sub_s))
    st.append(Spacer(1, 12 * mm))
    st.append(img('04_melding_detail.png'))
    st.append(Paragraph('ProMaster v2.20.0.02 — A.B.A.S.O.F.F.', cap_s))
    st.append(PageBreak())

    # ── 1 Inloggen ──
    st.append(Paragraph('1. Inloggen', h1_s))
    st.append(Paragraph(
        'Open ProMaster in de browser (op de computer of op je telefoon). '
        'Vul je <b>gebruikersnaam</b> en <b>wachtwoord</b> in en klik op inloggen. '
        'Je komt direct op je startpagina.', body_s))
    st.append(img('01_inloggen.png'))
    st.append(PageBreak())

    # ── 2 Notificaties ──
    st.append(Paragraph('2. Meldingen inschakelen (belangrijk!)', h1_s))
    st.append(Paragraph(
        'Zie je een oranje balk <b>\'Meldingen inschakelen\'</b>? Klik erop. '
        'Dan krijg je een melding op je computer of telefoon zodra er een nieuwe '
        'storingsmelding is of er iets met jouw werk gebeurt. '
        'Op de telefoon: voeg ProMaster toe aan je beginscherm — meldingen werken dan het best.', body_s))
    st.append(img('02_notificaties_banner.png'))
    st.append(Paragraph('Tip: zonder meldingen mis je nieuwe storingen. Zet dit altijd aan.', tip_s))
    st.append(PageBreak())

    # ── 3 Overzicht ──
    st.append(Paragraph('3. Overzicht van storingsmeldingen', h1_s))
    st.append(Paragraph(
        'Klik in het menu op <b>Storingen</b>. Je ziet <b>alle open en actieve meldingen</b> — '
        'ook die van andere monteurs. Zo weet je altijd wat er speelt.', body_s))
    st.append(img('03_meldingen_lijst.png'))
    st.append(Paragraph('De kolommen:', h2_s))
    data = [
        ['#', 'Het volgnummer van de melding.'],
        ['Titel', 'Korte omschrijving van het probleem.'],
        ['Machine', 'Waar het probleem zit.'],
        ['Melder', 'Wie het probleem gemeld heeft.'],
        ['Prioriteit', 'low / normal / high / critical — hoe dringend.'],
        ['Status', 'Waar de melding nu staat (zie statusoverzicht hieronder).'],
        ['Monteur', 'Wie eraan werkt.'],
        ['Acties', 'Klik op <b>Bekijken</b> om de melding te openen.'],
    ]
    rows = [[Paragraph(f'<b>{a}</b>', body_s), Paragraph(b, body_s)] for a, b in data]
    t = Table(rows, colWidths=[28 * mm, PW - 28 * mm])
    t.setStyle(TableStyle([
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [GREY, colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#dddddd')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    st.append(t)
    st.append(PageBreak())

    # ── 4 Aannemen ──
    st.append(Paragraph('4. Melding openen en aannemen', h1_s))
    st.append(Paragraph(
        'Open de melding via <b>Bekijken</b>. Lees de beschrijving en bekijk de foto\'s. '
        'Klik daarna op <b>Accepteren</b> — de melding is nu van jou en gaat naar '
        '\'in behandeling\'.', body_s))
    st.append(img('04_melding_detail.png'))
    st.append(Paragraph('Na het accepteren verandert de status:', h2_s))
    st.append(img('05_geaccepteerd.png'))
    st.append(PageBreak())

    # ── 5 Werken ──
    st.append(Paragraph('5. Werken aan de melding', h1_s))
    st.append(Paragraph(
        'Met <b>Status wijzigen</b> geef je aan hoe het werk ervoor staat: '
        '<b>In behandeling</b> (je bent bezig), <b>Wacht op onderdelen</b>, <b>Testen</b>, '
        'of <b>Opgelost</b> als de reparatie klaar is. '
        'Met <b>Werk starten</b> / <b>Werk stoppen</b> registreer je de tijd die je eraan besteedt.', body_s))
    st.append(Paragraph(
        'De <b>statushistorie</b> bovenaan de melding laat precies zien wat er gebeurd is '
        '(wie, wat, wanneer). Zo kan iedereen het werk volgen.', body_s))
    st.append(img('08_melding_tijdlijn.png'))
    st.append(PageBreak())

    # ── 6 Pauze ──
    st.append(Paragraph('6. Pauze', h1_s))
    st.append(Paragraph(
        'Kan je niet verder werken (bijv. wacht op een onderdeel of de machine moet draaien)? '
        'Klik op <b>Pause</b> en vul de reden in. Zo weet iedereen waarom het werk stilstaat.', body_s))
    st.append(img('06_pauzevenster.png'))
    st.append(PageBreak())

    # ── 7 Werkrapport ──
    st.append(Paragraph('7. Werkrapport — wat heb je gedaan?', h1_s))
    st.append(Paragraph(
        'Voeg bij elke reparatie een <b>werkrapport</b> toe: wat was het probleem, '
        'wat heb je gedaan, welke onderdelen heb je gebruikt. '
        'Klik op <b>Werkrapport aanmaken</b> en vul het formulier in. '
        'Je kunt hier ook <b>foto\'s</b> toevoegen van je werk.', body_s))
    st.append(img('07_werkrapport.png'))
    st.append(PageBreak())

    # ── 8 Foto's ──
    st.append(Paragraph('8. Foto\'s — wanneer voeg je ze toe?', h1_s))
    st.append(Paragraph(
        '<b>Bij het melden</b>: foto van het probleem (kapotte band, foutmelding, lekkage). '
        'Dit helpt de monteur om zich voor te bereiden.<br/>'
        '<b>Bij de reparatie</b>: foto van het gerepareerde onderdeel of het resultaat. '
        'Dit is het bewijs van je werk.', body_s))
    st.append(Paragraph(
        'Alle foto\'s van de melding verschijnen <b>automatisch in de werkbon (TWO)</b> '
        'onder \'Foto\'s van de melding\' — je hoeft ze niet opnieuw toe te voegen.', body_s))
    st.append(img('11_two_fotos.png', w=150 * mm))
    st.append(Paragraph('Foto\'s van de melding staan automatisch in de werkbon.', cap_s))
    st.append(PageBreak())

    # ── 9 Opgelost ──
    st.append(Paragraph('9. Reparatie klaar? Meld opgelost', h1_s))
    st.append(Paragraph(
        'Als de reparatie klaar is, klik op <b>Opgelost</b>. De melding gaat naar '
        '\'opgelost\' en wacht op acceptatie. '
        'De melder (of de leiding) accepteert het werk — zie de volgende stap.', body_s))
    st.append(img('09_opgelost.png'))
    st.append(PageBreak())

    # ── 10 TWO ──
    st.append(Paragraph('10. TWO — werkbon en handtekening', h1_s))
    st.append(Paragraph(
        'Bij elke storingsmelding (behalve prioriteit low) wordt automatisch een '
        '<b>TWO (werkbon)</b> aangemaakt. Open hem via de paarse knop bovenaan de melding.', body_s))
    st.append(img('10_two_werkbon.png'))
    st.append(Paragraph('Onderaan de werkbon staat het handtekeningblok:', h2_s))
    st.append(img('12_two_handtekening.png'))
    st.append(Paragraph(
        'Neem de telefoon/tablet mee naar de persoon die het gemeld heeft. '
        'Vul zijn/haar <b>naam</b> in en laat hem/haar <b>tekenen</b> op het scherm. '
        'Klik op <b>Ondertekenen en voltooien</b>.', body_s))
    st.append(Paragraph(
        'De handtekening betekent: <b>het werk is aanvaard</b>. Daarna sluiten de werkbon '
        'én de storingsmelding automatisch. Ook de leiding (hoofd technische dienst of admin) '
        'mag het werk accepteren.', tip_s))
    st.append(PageBreak())

    # ── 11 Mobiel ──
    st.append(Paragraph('11. Werken op je telefoon', h1_s))
    st.append(Paragraph(
        'Op de telefoon werkt alles hetzelfde: meldingen bekijken, aannemen, '
        'foto\'s maken en statussen wijzigen. Voeg ProMaster toe aan je beginscherm '
        'voor de beste ervaring en meldingen.', body_s))
    st.append(img('13_mobiel_start.png', w=72 * mm))
    st.append(Paragraph('Startpagina op de telefoon.', cap_s))
    st.append(img('15_mobiel_melding.png', w=72 * mm))
    st.append(Paragraph('Melding op de telefoon.', cap_s))
    st.append(PageBreak())

    # ── 12 Statusoverzicht ──
    st.append(Paragraph('12. Statusoverzicht', h1_s))
    srows = [
        ['Status', 'Betekenis', 'Wie'],
        ['Nieuw', 'Melding net aangemaakt, nog niet aangenomen.', 'Melder / iedereen'],
        ['Geaccepteerd', 'Monteur heeft de melding aangenomen.', 'Monteur'],
        ['In behandeling', 'Monteur is bezig met de reparatie.', 'Monteur'],
        ['Pauze', 'Werk ligt stil — reden is ingevuld.', 'Monteur'],
        ['Wacht op onderdelen', 'Onderdeel besteld, wachten op levering.', 'Monteur'],
        ['Testen', 'Reparatie klaar, machine wordt getest.', 'Monteur'],
        ['Opgelost', 'Reparatie klaar — wacht op acceptatie.', 'Monteur'],
        ['Gesloten', 'Werk aanvaard en afgehandeld.', 'Melder / leiding'],
    ]
    stbl = [[Paragraph(f'<b>{c}</b>' if i == 0 else c, body_s) for c in row] for i, row in enumerate(srows)]
    t2 = Table(stbl, colWidths=[38 * mm, 92 * mm, 40 * mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [GREY, colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#dddddd')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    st.append(t2)
    st.append(Spacer(1, 6 * mm))

    st.append(Paragraph('Korte checklist voor elke storing', h1_s))
    for line in [
        '1. Melding openen via Bekijken en de beschrijving lezen.',
        '2. <b>Accepteren</b> — de melding is nu van jou.',
        '3. Reparatie uitvoeren — <b>Werk starten</b> voor tijdregistratie.',
        '4. Foto\'s toevoegen: probleem en resultaat.',
        '5. <b>Werkrapport</b> invullen — wat heb je gedaan?',
        '6. Kan je niet verder? <b>Pause</b> met reden.',
        '7. Klaar? <b>Opgelost</b> melden.',
        '8. Handtekening van de melder op de TWO — alles sluit automatisch.',
    ]:
        st.append(Paragraph(line, body_s))

    doc.build(st, onFirstPage=chrome, onLaterPages=chrome)
    print('PDF OK:', OUT, os.path.getsize(OUT), 'bytes')


if __name__ == '__main__':
    main()

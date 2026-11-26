# -*- coding: utf-8 -*-
"""Build the Dutch mechanic training deck (ProMaster) from workflow screenshots."""
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN
from pptx.dml.color import RGBColor
from PIL import Image

ROOT = r'D:\ИИ\CRM_Мастерская'
SHOTS = os.path.join(ROOT, 'work', 'manual_monteur', 'shots')
OUT = os.path.join(ROOT, 'static', 'docs', 'ProMaster_Handleiding_Monteur_NL.pptx')

NAVY = RGBColor(0x2c, 0x3e, 0x50)
BLUE = RGBColor(0x34, 0x98, 0xdb)
GREEN = RGBColor(0x27, 0xae, 0x60)
ORANGE = RGBColor(0xe6, 0x7e, 0x22)
GREY = RGBColor(0x7a, 0x7a, 0x7a)
LIGHT = RGBColor(0xf2, 0xf4, 0xf7)
WHITE = RGBColor(0xff, 0xff, 0xff)
FONT = 'Calibri'


def add_title(slide, text, sub=None):
    tb = slide.shapes.add_textbox(Inches(0.55), Inches(0.3), Inches(12.2), Inches(0.8))
    p = tb.text_frame.paragraphs[0]
    p.text = text
    r = p.runs[0]
    r.font.size = Pt(30)
    r.font.bold = True
    r.font.name = FONT
    r.font.color.rgb = NAVY
    ln = slide.shapes.add_shape(1, Inches(0.55), Inches(1.12), Inches(2.2), Inches(0.045))
    ln.fill.solid()
    ln.fill.fore_color.rgb = BLUE
    ln.line.fill.background()
    if sub:
        st = slide.shapes.add_textbox(Inches(0.55), Inches(1.22), Inches(12.2), Inches(0.5))
        sp = st.text_frame.paragraphs[0]
        sp.text = sub
        sr = sp.runs[0]
        sr.font.size = Pt(16)
        sr.font.name = FONT
        sr.font.color.rgb = GREY


def add_text(slide, lines, left, top, width, height, size=18, color=None, bold_first=False):
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        r = p.runs[0]
        r.font.size = Pt(size)
        r.font.name = FONT
        r.font.color.rgb = color or NAVY
        if bold_first and i == 0:
            r.font.bold = True
        p.space_after = Pt(8)
    return tb


def add_shot(slide, name, left, top, width):
    path = os.path.join(SHOTS, name)
    iw, ih = Image.open(path).size
    h = width * ih / iw
    return slide.shapes.add_picture(path, Inches(left), Inches(top), width=Inches(width))


def add_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def add_box(slide, left, top, width, height, title, body, accent=BLUE):
    box = slide.shapes.add_shape(5, Inches(left), Inches(top), Inches(width), Inches(height))
    box.fill.solid()
    box.fill.fore_color.rgb = LIGHT
    box.line.color.rgb = accent
    box.line.width = Pt(1.25)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.15)
    tf.margin_top = Inches(0.1)
    p = tf.paragraphs[0]
    p.text = title
    p.runs[0].font.size = Pt(15)
    p.runs[0].font.bold = True
    p.runs[0].font.name = FONT
    p.runs[0].font.color.rgb = accent
    p2 = tf.add_paragraph()
    p2.text = body
    p2.runs[0].font.size = Pt(12.5)
    p2.runs[0].font.name = FONT
    p2.runs[0].font.color.rgb = NAVY


def main():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1 Cover
    s = prs.slides.add_slide(blank)
    bg = s.shapes.add_shape(1, 0, 0, Inches(13.333), Inches(7.5))
    bg.fill.solid()
    bg.fill.fore_color.rgb = NAVY
    bg.line.fill.background()
    tb = s.shapes.add_textbox(Inches(0.8), Inches(2.2), Inches(6.2), Inches(2.6))
    p = tb.text_frame.paragraphs[0]
    p.text = 'Handleiding voor monteurs'
    p.runs[0].font.size = Pt(44)
    p.runs[0].font.bold = True
    p.runs[0].font.name = FONT
    p.runs[0].font.color.rgb = WHITE
    p2 = tb.text_frame.add_paragraph()
    p2.text = 'ProMaster — storingsmeldingen, werkbonnen en foto\'s'
    p2.runs[0].font.size = Pt(20)
    p2.runs[0].font.name = FONT
    p2.runs[0].font.color.rgb = RGBColor(0xbd, 0xc3, 0xc7)
    p3 = tb.text_frame.add_paragraph()
    p3.text = ' '
    p4 = tb.text_frame.add_paragraph()
    p4.text = 'A.B.A.S.O.F.F. — versie 2.20.0.02'
    p4.runs[0].font.size = Pt(14)
    p4.runs[0].font.name = FONT
    p4.runs[0].font.color.rgb = RGBColor(0x95, 0xa5, 0xa6)
    add_shot(s, '04_melding_detail.png', 7.1, 1.75, 5.7)
    add_notes(s, 'Welkom. In deze handleiding leren we hoe je als monteur werkt met ProMaster: storingen aannemen, repareren, foto\'s en de werkbon met handtekening.')

    # 2 Agenda
    s = prs.slides.add_slide(blank)
    add_title(s, 'Wat leer je vandaag?')
    items = [
        ('1. Inloggen', 'Snel starten op pc of telefoon', BLUE),
        ('2. Meldingen', 'Alle storingen zien en aannemen', BLUE),
        ('3. Werken', 'Statussen, pauze en werkrapport', GREEN),
        ('4. Foto\'s', 'Wanneer en waarom foto\'s maken', GREEN),
        ('5. TWO & handtekening', 'Werkbon en acceptatie van het werk', ORANGE),
        ('6. Afsluiten', 'Alles sluit automatisch na de handtekening', ORANGE),
    ]
    x0, y0 = 0.7, 1.7
    for i, (t, b, c) in enumerate(items):
        col, row = i % 3, i // 3
        add_box(s, x0 + col * 4.15, y0 + row * 2.3, 3.85, 1.95, t, b, c)
    add_notes(s, 'Overzicht van de sessie. Leg uit dat alles in hetzelfde scherm gebeurt: melding → reparatie → foto\'s → werkbon → handtekening.')

    # 3 Inloggen
    s = prs.slides.add_slide(blank)
    add_title(s, '1. Inloggen', 'Open ProMaster in de browser en log in met je eigen gegevens')
    add_shot(s, '01_inloggen.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Ga naar het ProMaster-adres',
        '• Vul gebruikersnaam en wachtwoord in',
        '• Klik op inloggen',
        '• Je komt direct op je startpagina',
    ], 0.6, 2.0, 5.6, 3.5, size=18)
    add_notes(s, 'Laat zien hoe je inlogt. Iedere monteur heeft eigen inloggegevens — deel ze nooit.')

    # 4 Notificaties
    s = prs.slides.add_slide(blank)
    add_title(s, '2. Meldingen inschakelen — belangrijk!', 'Zonder meldingen mis je nieuwe storingen')
    add_shot(s, '02_notificaties_banner.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Klik op de oranje balk',
        '  «Meldingen inschakelen»',
        '• Op de telefoon: voeg ProMaster',
        '  toe aan je beginschelm',
        '• Je krijgt dan een melding bij',
        '  nieuwe storingen en updates',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    tip = s.shapes.add_shape(5, Inches(0.6), Inches(5.5), Inches(5.6), Inches(1.1))
    tip.fill.solid()
    tip.fill.fore_color.rgb = RGBColor(0xea, 0xfa, 0xf1)
    tip.line.color.rgb = GREEN
    ttf = tip.text_frame
    ttf.word_wrap = True
    ttf.paragraphs[0].text = 'Doe dit als eerste na het inloggen!'
    ttf.paragraphs[0].runs[0].font.size = Pt(15)
    ttf.paragraphs[0].runs[0].font.bold = True
    ttf.paragraphs[0].runs[0].font.name = FONT
    ttf.paragraphs[0].runs[0].font.color.rgb = GREEN
    add_notes(s, 'Nadruk leggen: meldingen aanzetten. Op de telefoon moet ProMaster op het beginscherm staan voor meldingen.')

    # 5 Overzicht
    s = prs.slides.add_slide(blank)
    add_title(s, '3. Alle storingen in één overzicht', 'Je ziet alle open en actieve meldingen — ook van andere monteurs')
    add_shot(s, '03_meldingen_lijst.png', 1.35, 1.75, 10.6)
    add_notes(s, 'Het overzicht toont alle lopende storingen. Kolommen: nummer, titel, machine, melder, prioriteit, status, monteur, datum. Open een melding via Bekijken.')

    # 6 Aannemen
    s = prs.slides.add_slide(blank)
    add_title(s, '4. Melding openen en aannemen', 'Lees de beschrijving, bekijk de foto\'s en klik op Accepteren')
    add_shot(s, '04_melding_detail.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Klik op Bekijken',
        '• Lees beschrijving en foto\'s',
        '• Klik op Accepteren:',
        '  de melding is nu van jou',
        '• De status gaat naar',
        '  «in behandeling»',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'Accepteren betekent: ik ga eraan werken. Daarna is de melding van jou en zie je dat in de status.')

    # 7 Status flow
    s = prs.slides.add_slide(blank)
    add_title(s, '5. Zo loopt een melding', 'Van nieuw tot gesloten — jij bepaalt de status')
    steps = ['Nieuw', 'Geaccepteerd', 'In behandeling', 'Opgelost', 'Gesloten']
    cols = [BLUE, BLUE, GREEN, GREEN, NAVY]
    x = 0.55
    for i, (st, c) in enumerate(zip(steps, cols)):
        box = s.shapes.add_shape(5, Inches(x), Inches(2.2), Inches(2.15), Inches(1.1))
        box.fill.solid()
        box.fill.fore_color.rgb = c
        box.line.fill.background()
        tf = box.text_frame
        tf.paragraphs[0].text = st
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        tf.paragraphs[0].runs[0].font.size = Pt(16)
        tf.paragraphs[0].runs[0].font.bold = True
        tf.paragraphs[0].runs[0].font.name = FONT
        tf.paragraphs[0].runs[0].font.color.rgb = WHITE
        if i < len(steps) - 1:
            ar = s.shapes.add_shape(1, Inches(x + 2.2), Inches(2.6), Inches(0.35), Inches(0.12))
            ar.fill.solid()
            ar.fill.fore_color.rgb = GREY
            ar.line.fill.background()
        x += 2.55
    add_text(s, [
        'Extra statussen onderweg: Pause (werk ligt stil — met reden), Wacht op onderdelen, Testen.',
        'Opgelost = reparatie klaar. Gesloten = werk aanvaard (handtekening of leiding).',
    ], 0.6, 4.1, 12.1, 1.6, size=18)
    add_notes(s, 'Leg de statusoverloop uit. Monteur beheert de status tot Opgelost; acceptatie (handtekening) sluit de melding.')

    # 8 Werken / tijdlijn
    s = prs.slides.add_slide(blank)
    add_title(s, '6. Werken aan de melding', 'Status wijzigen · Werk starten/stoppen · Alles blijft zichtbaar in de tijdlijn')
    add_shot(s, '08_melding_tijdlijn.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Status wijzigen: in behandeling,',
        '  wacht op onderdelen, testen…',
        '• Werk starten/stoppen =',
        '  tijdregistratie van je werk',
        '• Tijdlijn bovenaan toont wie',
        '  wat en wanneer gedaan heeft',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'Moedig aan om Werk starten/stoppen te gebruiken — zo is de bestede tijd bekend. De tijdlijn is het geheugen van de melding.')

    # 9 Pauze
    s = prs.slides.add_slide(blank)
    add_title(s, '7. Pauze met reden', 'Kan je niet verder? Zet de melding op pauze en leg uit waarom')
    add_shot(s, '06_pauzevenster.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Klik op Pause',
        '• Vul de reden in:',
        '  bijv. wacht op onderdeel,',
        '  machine moet draaien',
        '• Iedereen ziet waarom',
        '  het werk stilstaat',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'Pauze is geen stop: het is communicatie. Zonder reden weten collega\'s niet of ze kunnen overnemen.')

    # 10 Werkrapport
    s = prs.slides.add_slide(blank)
    add_title(s, '8. Werkrapport — wat heb je gedaan?', 'Beschrijf je reparatie en voeg foto\'s van het resultaat toe')
    add_shot(s, '07_werkrapport.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Klik op Werkrapport aanmaken',
        '• Wat was het probleem?',
        '• Wat heb je gedaan?',
        '• Welke onderdelen gebruikt?',
        '• Foto\'s van het resultaat',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'Zonder werkrapport kan een melding niet correct afgesloten worden. Het is ook bewijs van je werk.')

    # 11 Foto's
    s = prs.slides.add_slide(blank)
    add_title(s, '9. Foto\'s — wanneer?', 'Foto\'s van de melding komen automatisch in de werkbon')
    add_box(s, 0.6, 1.8, 5.9, 2.2, 'Bij het melden', 'Foto van het probleem: kapotte band, foutmelding, lekkage. Zo kan de monteur zich voorbereiden.', BLUE)
    add_box(s, 0.6, 4.2, 5.9, 2.2, 'Bij de reparatie', 'Foto van het gerepareerde onderdeel of het resultaat. Dit is het bewijs van je werk.', GREEN)
    add_shot(s, '11_two_fotos.png', 6.9, 1.8, 5.9)
    add_notes(s, 'Twee momenten: probleem bij melden, resultaat bij reparatie. Automatisch zichtbaar in de TWO — niet opnieuw uploaden.')

    # 12 Opgelost
    s = prs.slides.add_slide(blank)
    add_title(s, '10. Reparatie klaar? Meld Opgelost', 'Daarna volgt de acceptatie door de melder of de leiding')
    add_shot(s, '09_opgelost.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Klik op Opgelost',
        '• De melding wacht op',
        '  acceptatie van het werk',
        '• De melder (of leiding)',
        '  tekent voor de werkbon',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'Opgelost is jouw laatste stap. Acceptatie komt van de melder of leiding — via de handtekening op de TWO.')

    # 13 TWO
    s = prs.slides.add_slide(blank)
    add_title(s, '11. TWO — de werkbon', 'Automatisch aangemaakt bij elke storingsmelding (behalve low)')
    add_shot(s, '10_two_werkbon.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Open via de paarse knop',
        '  bovenaan de melding',
        '• Machine, beschrijving,',
        '  foto\'s en controlelijst',
        '• Onderaan: handtekening',
    ], 0.6, 2.0, 5.6, 3.8, size=18)
    add_notes(s, 'De TWO is het officiële werkdocument. Het nummer TWO-... zie je ook op de knop in de melding.')

    # 14 Handtekening
    s = prs.slides.add_slide(blank)
    add_title(s, '12. Handtekening = alles sluit', 'Neem het toestel mee naar de melder en laat hem/haar tekenen')
    add_shot(s, '12_two_handtekening.png', 6.6, 1.85, 6.2)
    add_text(s, [
        '• Naam van de persoon die',
        '  het werk aanvaardt',
        '• Laat hem/haar tekenen',
        '  op het scherm',
        '• Ondertekenen en voltooien',
        '• Werkbon én melding sluiten',
        '  automatisch',
    ], 0.6, 2.0, 5.6, 4.4, size=17)
    add_notes(s, 'Dit is de belangrijkste stap: de handtekening van de melder. De leiding (hoofd TD of admin) mag ook accepteren.')

    # 15 Mobiel
    s = prs.slides.add_slide(blank)
    add_title(s, '13. Werken op je telefoon', 'Alles werkt hetzelfde — voeg ProMaster toe aan je beginscherm')
    add_shot(s, '13_mobiel_start.png', 3.2, 1.7, 3.2)
    add_shot(s, '15_mobiel_melding.png', 6.9, 1.7, 3.2)
    add_text(s, [
        'Meldingen bekijken · aannemen · foto\'s maken · statussen wijzigen — alles op je telefoon.',
    ], 0.6, 6.35, 12.1, 0.8, size=16, color=GREY)
    add_notes(s, 'Monteurs werken vaak op de telefoon. Benadruk beginscherm + meldingen.')

    # 16 Statusoverzicht
    s = prs.slides.add_slide(blank)
    add_title(s, 'Statusoverzicht')
    rows = [
        ('Nieuw', 'Net aangemaakt, nog niet aangenomen', 'Iedereen'),
        ('Geaccepteerd', 'Monteur heeft de melding aangenomen', 'Monteur'),
        ('In behandeling', 'Monteur is bezig met de reparatie', 'Monteur'),
        ('Pauze', 'Werk ligt stil — reden ingevuld', 'Monteur'),
        ('Wacht op onderdelen', 'Onderdeel besteld, wachten op levering', 'Monteur'),
        ('Testen', 'Reparatie klaar, machine wordt getest', 'Monteur'),
        ('Opgelost', 'Reparatie klaar — wacht op acceptatie', 'Monteur'),
        ('Gesloten', 'Werk aanvaard en afgehandeld', 'Melder / leiding'),
    ]
    tbl = s.shapes.add_table(len(rows) + 1, 3, Inches(0.6), Inches(1.6), Inches(12.1), Inches(5.2)).table
    tbl.columns[0].width = Inches(3.2)
    tbl.columns[1].width = Inches(6.3)
    tbl.columns[2].width = Inches(2.6)
    for c, h in enumerate(['Status', 'Betekenis', 'Wie']):
        cell = tbl.cell(0, c)
        cell.text = h
        cell.text_frame.paragraphs[0].runs[0].font.bold = True
        cell.text_frame.paragraphs[0].runs[0].font.size = Pt(15)
        cell.text_frame.paragraphs[0].runs[0].font.name = FONT
    for r, (a, b, cc) in enumerate(rows, start=1):
        for c, v in enumerate((a, b, cc)):
            cell = tbl.cell(r, c)
            cell.text = v
            cell.text_frame.paragraphs[0].runs[0].font.size = Pt(13.5)
            cell.text_frame.paragraphs[0].runs[0].font.name = FONT
    add_notes(s, 'Overzicht om op terug te vallen. Print deze dia eventueel uit voor bij de werkplek.')

    # 17 Checklist
    s = prs.slides.add_slide(blank)
    add_title(s, 'Checklist — elke storing in 8 stappen')
    steps8 = [
        '1. Melding openen via Bekijken en de beschrijving lezen',
        '2. Accepteren — de melding is nu van jou',
        '3. Reparatie uitvoeren — Werk starten voor tijdregistratie',
        '4. Foto\'s toevoegen: probleem en resultaat',
        '5. Werkrapport invullen — wat heb je gedaan?',
        '6. Kan je niet verder? Pause met reden',
        '7. Klaar? Opgelost melden',
        '8. Handtekening van de melder op de TWO — alles sluit',
    ]
    add_text(s, steps8, 0.8, 1.7, 11.7, 5.4, size=20)
    add_notes(s, 'Loop de checklist kort door. Dit is het geheugensteuntje voor de praktijk.')

    prs.save(OUT)
    print('PPTX OK:', OUT, os.path.getsize(OUT), 'bytes,', len(prs.slides.__iter__.__self__._sldIdLst), 'slides')


if __name__ == '__main__':
    main()

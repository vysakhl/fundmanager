"""PDF export of the currently open debits (landscape A4)."""
import io
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

FONT_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'static', 'fonts')
_REG = os.path.join(FONT_DIR, 'DejaVuSans.ttf')
_BOLD = os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf')

if os.path.exists(_REG) and os.path.exists(_BOLD):
    pdfmetrics.registerFont(TTFont('Body', _REG))
    pdfmetrics.registerFont(TTFont('Body-Bold', _BOLD))
    FONT, FONT_BOLD, RUPEE = 'Body', 'Body-Bold', '₹'
else:  # bundled font missing: Helvetica has no ₹ glyph, so fall back to "Rs."
    FONT, FONT_BOLD, RUPEE = 'Helvetica', 'Helvetica-Bold', 'Rs. '


def _money(v):
    return f'{RUPEE}{v:,.2f}'


def build_open_debits_pdf(rows, generated_on, fund_name='Money Mafia — Kuri Ledger'):
    """rows: list of dicts with member, principal, date, months, interest,
    outstanding, paid, overdue. Returns the PDF as bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title='Open Debits',
    )
    title = ParagraphStyle('t', fontName=FONT_BOLD, fontSize=16, leading=20, textColor=colors.HexColor('#16233f'))
    sub = ParagraphStyle('s', fontName=FONT, fontSize=9, leading=12, textColor=colors.HexColor('#5b6577'))

    story = [
        Paragraph(f'{fund_name} — Open Debits', title),
        Paragraph(f'As of {generated_on} &nbsp;•&nbsp; {len(rows)} open debit(s)', sub),
        Spacer(1, 6 * mm),
    ]

    header = ['#', 'Member', 'Principal', 'Date', 'Month #', 'Interest', 'Outstanding', 'Paid', 'Status']
    data = [header]
    for i, r in enumerate(rows, 1):
        data.append([
            str(i), r['member'], _money(r['principal']), r['date'], str(r['months']),
            _money(r['interest']), _money(r['outstanding']), _money(r['paid']),
            'Overdue' if r['overdue'] else 'Open',
        ])
    if rows:
        data.append([
            '', 'Total', _money(sum(r['principal'] for r in rows)), '', '',
            _money(sum(r['interest'] for r in rows)), _money(sum(r['outstanding'] for r in rows)),
            _money(sum(r['paid'] for r in rows)), '',
        ])
    else:
        data.append(['', 'No open debits', '', '', '', '', '', '', ''])

    widths = [10 * mm, 46 * mm, 32 * mm, 26 * mm, 18 * mm, 30 * mm, 34 * mm, 30 * mm, 24 * mm]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8dfc6')),
        ('FONTNAME', (0, 0), (-1, 0), FONT_BOLD),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#16233f')),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('ALIGN', (5, 0), (7, -1), 'RIGHT'),
        ('ALIGN', (4, 0), (4, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cdbf96')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    for i, r in enumerate(rows, 1):
        if r['overdue']:
            style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f4d9d2')))
    if rows:
        last = len(data) - 1
        style += [
            ('FONTNAME', (0, last), (-1, last), FONT_BOLD),
            ('BACKGROUND', (0, last), (-1, last), colors.HexColor('#f5efe0')),
        ]
    table.setStyle(TableStyle(style))
    story.append(table)

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(colors.HexColor('#5b6577'))
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f'Page {d.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()

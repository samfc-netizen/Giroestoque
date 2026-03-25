import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    import pdfplumber
except Exception:
    pdfplumber = None

st.set_page_config(page_title="Análise de Giro por PDF", layout="wide")

UNIT_TOKENS = {
    "UN", "KG", "PC", "PCT", "PT", "CX", "FD", "RL", "LT", "L", "ML",
    "GL", "BD", "SC", "PAR", "JG", "KIT", "CJ", "TB", "EMB"
}
NUM_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$|^-?\d+,\d{2}$|^-?\d+$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
CODE_RE = re.compile(r"^(\d{4,6})\s+(.*)$")
MONTH_HEADER_RE = re.compile(r"REFERENTE AOS MESES:\s*([^\n]+)")

STORE_MAP: Dict[str, str] = {
    "001": "GUARÁ",
    "004": "ADE",
    "006": "GAMA",
    "008": "LUZIÂNIA",
    "009": "ÚNICA",
    "012": "SOFNORTE",
    "013": "CEILÂNDIA",
    "014": "S IA",
    "015": "UNAÍ",
    "016": "AG LINDAS",
    "022": "GUARÁ",
    "024": "LUZIÂNIA",
}

ACTION_TRANSFER = "Sugestão de transferência"
ACTION_SALES = "Sem giro no grupo"
ACTION_RECENT = "Produto puxado recentemente"


@dataclass
class ParseResult:
    df: pd.DataFrame
    months: List[str]
    errors: List[str]



def br_to_float(value: str) -> float:
    value = str(value).strip()
    if not value:
        return 0.0
    value = value.replace('.', '').replace(',', '.')
    try:
        return float(value)
    except Exception:
        return 0.0



def number_br(value: float) -> str:
    try:
        return f"{float(value):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(value)



def parse_months_from_pages(pages_text: List[str]) -> List[str]:
    sample_text = "\n".join(pages_text[:5])
    match = MONTH_HEADER_RE.search(sample_text)
    if not match:
        return ["Mês 1", "Mês 2", "Mês 3", "Mês 4"]
    months = [m.strip() for m in match.group(1).split(',') if m.strip()]
    months = list(reversed(months))
    return months[:4] if len(months) >= 4 else ["Mês 1", "Mês 2", "Mês 3", "Mês 4"]



def split_description_and_unit(rest: str) -> Optional[Tuple[str, str, List[str]]]:
    tokens = rest.split()
    for i, token in enumerate(tokens):
        if token in UNIT_TOKENS:
            desc = " ".join(tokens[:i]).strip()
            right = tokens[i + 1 :]
            if desc and right:
                return desc, token, right
    return None





def parse_br_date(value: str) -> Optional[date]:
    value = str(value).strip()
    if not value:
        return None
    for fmt in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except Exception:
            continue
    return None

def normalize_store(company: str) -> Tuple[str, str]:
    code = company.split(" - ")[0].strip() if " - " in company else company.strip()
    code = code.zfill(3) if code.isdigit() else code
    mapped = STORE_MAP.get(code, company.replace(f"{code} - ", "").strip() if " - " in company else company.strip())
    return code, f"{code} - {mapped}"



def parse_product_line(line: str, company: str, line_name: str, group_name: str, months: List[str]) -> Optional[dict]:
    m = CODE_RE.match(line.strip())
    if not m:
        return None
    code = m.group(1)
    rest = m.group(2).strip()

    split_res = split_description_and_unit(rest)
    if not split_res:
        return None
    description, unit, tail = split_res
    tail = [t for t in tail if t]
    if len(tail) < 11:
        return None

    date_idx = None
    for i, token in enumerate(tail):
        if DATE_RE.match(token):
            date_idx = i
            break

    if date_idx is not None and date_idx >= 9:
        numeric_left = tail[:date_idx]
        numeric_right = tail[date_idx + 1 :]
        if len(numeric_left) < 9 or len(numeric_right) < 2:
            return None
        month_vals = numeric_left[:4]
        media = numeric_left[4]
        previ30 = numeric_left[5]
        estoque = numeric_left[6]
        sugestao = numeric_left[7]
        pr_ult_comp = numeric_left[8]
        dt_ult_comp = tail[date_idx]
        pr_venda = numeric_right[0]
        lucro = numeric_right[1]
    else:
        month_vals = tail[:4]
        media = tail[4]
        previ30 = tail[5]
        estoque = tail[6]
        sugestao = tail[7]
        pr_ult_comp = tail[8]
        dt_ult_comp = ""
        pr_venda = tail[9]
        lucro = tail[10]

    numeric_candidates = month_vals + [media, previ30, estoque, sugestao, pr_ult_comp, pr_venda, lucro]
    if not all(NUM_RE.match(x) for x in numeric_candidates):
        return None

    month_labels = (months + ["Mês 1", "Mês 2", "Mês 3", "Mês 4"])[:4]
    codigo_loja, loja = normalize_store(company)
    return {
        "empresa_raw": company,
        "empresa": loja,
        "codigo_loja": codigo_loja,
        "linha": line_name,
        "grupo": group_name,
        "codigo_item": code,
        "descricao": description,
        "unidade": unit,
        month_labels[0]: br_to_float(month_vals[0]),
        month_labels[1]: br_to_float(month_vals[1]),
        month_labels[2]: br_to_float(month_vals[2]),
        month_labels[3]: br_to_float(month_vals[3]),
        "media": br_to_float(media),
        "previ30": br_to_float(previ30),
        "estoque": br_to_float(estoque),
        "sugestao": br_to_float(sugestao),
        "pr_ult_comp": br_to_float(pr_ult_comp),
        "dt_ult_comp": dt_ult_comp,
        "pr_venda": br_to_float(pr_venda),
        "lucro_pct": br_to_float(lucro),
    }



def _extract_text_with_pypdf(raw: bytes) -> Tuple[List[str], int]:
    if PdfReader is None:
        raise RuntimeError("pypdf não está disponível")
    reader = PdfReader(io.BytesIO(raw))
    texts = []
    total = len(reader.pages)
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return texts, total



def _extract_text_with_pdfplumber(raw: bytes) -> Tuple[List[str], int]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber não está disponível")
    texts = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return texts, total


@st.cache_data(show_spinner=False)
def extract_text_pages_cached(raw: bytes) -> Tuple[List[str], int, str]:
    try:
        texts, total = _extract_text_with_pypdf(raw)
        return texts, total, "PyPDF"
    except Exception:
        texts, total = _extract_text_with_pdfplumber(raw)
        return texts, total, "pdfplumber"


@st.cache_data(show_spinner=False)
def parse_pdf_bytes(raw: bytes) -> ParseResult:
    pages_text, _, _ = extract_text_pages_cached(raw)
    months = parse_months_from_pages(pages_text)
    errors: List[str] = []
    rows: List[dict] = []

    company = ""
    line_name = ""
    group_name = ""

    ignore_prefixes = (
        "COD.", "UNICA ATACADISTA", "RELATORIO DO GIRO", "REFERENTE AOS MESES:", "PAGINA ",
        "FORNECEDOR:", "LINHA/GRUPO:", "UNIDADE:", "PRODUTO:", "MARCA:", "REFERENCIA:",
        "DESCRICAO:", "DIAS DE ABASTECIMENTO:", "EMPRESA:", "NIVEL DE GIRO:", "IMPRIME ITENS",
        "TABELA:", "PEDIDOS DE VENDA:", "PREÇO ÚLTIMA", "SALDOS:"
    )

    for text in pages_text:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("EMPRESA :"):
                company = line.replace("EMPRESA :", "").strip()
                continue
            if line.startswith("LINHA:"):
                line_name = line.replace("LINHA:", "").strip()
                continue
            if line.startswith("GRUPO:"):
                group_name = line.replace("GRUPO:", "").strip()
                continue
            if line.startswith(ignore_prefixes):
                continue
            row = parse_product_line(line, company, line_name, group_name, months)
            if row:
                rows.append(row)

    if not rows:
        errors.append("Nenhum item foi estruturado. Verifique se o PDF segue o mesmo padrão do relatório de giro.")
        return ParseResult(pd.DataFrame(), months, errors)

    df = pd.DataFrame(rows)
    df = df[df["codigo_item"].astype(str).str.len() >= 4].copy()
    df["item"] = df["codigo_item"].astype(str) + " - " + df["descricao"].astype(str)
    return ParseResult(df, months, errors)



def analyze_critical_items(df: pd.DataFrame, months: List[str], low_avg_limit: float, high_stock_limit: float, min_absorption: float, recent_purchase_days: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["cobertura_meses"] = work.apply(lambda x: (x["estoque"] / x["media"]) if x["media"] > 0 else 9999, axis=1)
    work["item_critico"] = (work["media"] <= low_avg_limit) & (work["estoque"] >= high_stock_limit)
    critical = work[work["item_critico"]].copy()

    if critical.empty:
        return critical

    result_rows = []
    cutoff_date = date.today() - timedelta(days=int(recent_purchase_days))
    for _, row in critical.iterrows():
        same_item = work[work["codigo_item"] == row["codigo_item"]].copy()
        other_stores = same_item[same_item["empresa"] != row["empresa"]].copy()
        required_qty = max(float(min_absorption), float(row["estoque"]) * 0.30)
        dt_compra = parse_br_date(row.get("dt_ult_comp", ""))

        destino = ""
        destino_media = 0.0
        destino_estoque = 0.0
        destino_previ30 = 0.0

        if dt_compra and dt_compra >= cutoff_date:
            action = ACTION_RECENT
            recommendation = "Produto puxado para loja recentemente."
            motivo = (
                f"Baixo giro na origem: média {number_br(row['media'])} e estoque {number_br(row['estoque'])}. "
                f"Sem ação sugerida porque a última compra foi em {dt_compra.strftime('%d/%m/%Y')}, dentro da janela de {recent_purchase_days} dias."
            )
        else:
            eligible = other_stores[other_stores["media"] >= required_qty].copy()
            if not eligible.empty:
                eligible["score_destino"] = eligible["media"] - eligible["estoque"]
                eligible = eligible.sort_values(["score_destino", "media", "estoque"], ascending=[False, False, True])
                best = eligible.iloc[0]
                action = ACTION_TRANSFER
                recommendation = (
                    f"Transferir para {best['empresa']}, pois essa loja possui média {number_br(best['media'])} ao mês, "
                    f"estoque atual {number_br(best['estoque'])} e consegue absorver pelo menos {number_br(required_qty)} por mês."
                )
                motivo = (
                    f"Baixo giro na origem: média {number_br(row['media'])} e estoque {number_br(row['estoque'])}."
                )
                destino = best["empresa"]
                destino_media = best["media"]
                destino_estoque = best["estoque"]
                destino_previ30 = best["previ30"]
            else:
                action = ACTION_SALES
                recommendation = "Produto sem giro no grupo, fazer ação de vendas urgente!"
                motivo = (
                    f"Baixo giro na origem: média {number_br(row['media'])} e estoque {number_br(row['estoque'])}. "
                    f"Nenhuma outra loja apresentou absorção mínima de {number_br(required_qty)} por mês."
                )

        months_dict = {m: row.get(m, 0.0) for m in months[:4]}
        result_rows.append(
            {
                "status": "Crítico",
                "acao": action,
                "motivo": motivo,
                "recomendacao": recommendation,
                "empresa_origem": row["empresa"],
                "codigo_loja_origem": row["codigo_loja"],
                "linha": row["linha"],
                "grupo": row["grupo"],
                "codigo_item": row["codigo_item"],
                "descricao": row["descricao"],
                "item": row["item"],
                **months_dict,
                "media": row["media"],
                "previ30": row["previ30"],
                "estoque": row["estoque"],
                "sugestao_relatorio": row["sugestao"],
                "cobertura_meses": row["cobertura_meses"],
                "absorcao_minima_exigida": required_qty,
                "empresa_destino": destino,
                "media_destino": destino_media,
                "estoque_destino": destino_estoque,
                "previ30_destino": destino_previ30,
                "dt_ult_comp": row.get("dt_ult_comp", ""),
                "pr_venda": row["pr_venda"],
                "lucro_pct": row["lucro_pct"],
            }
        )

    result = pd.DataFrame(result_rows)
    if not result.empty:
        result = result.sort_values(["empresa_origem", "acao", "linha", "estoque"], ascending=[True, True, True, False])
    return result



def build_network_view(df: pd.DataFrame, codigo_item: str, months: List[str]) -> pd.DataFrame:
    net = df[df["codigo_item"] == str(codigo_item)].copy()
    if net.empty:
        return net
    cols = ["empresa", "linha", "grupo", "codigo_item", "descricao"] + months[:4] + ["media", "previ30", "estoque", "sugestao", "dt_ult_comp", "pr_venda", "lucro_pct"]
    available = [c for c in cols if c in net.columns]
    net = net[available].sort_values(["media", "estoque"], ascending=[False, False])
    return net



def format_table(df: pd.DataFrame, months: List[str]) -> pd.DataFrame:
    out = df.copy()
    num_cols = [
        c for c in months[:4] + [
            "media", "previ30", "estoque", "sugestao_relatorio", "cobertura_meses",
            "absorcao_minima_exigida", "media_destino", "estoque_destino", "previ30_destino",
            "pr_venda", "lucro_pct"
        ] if c in out.columns
    ]
    for c in num_cols:
        out[c] = out[c].apply(number_br)
    return out



def page_summary(df: pd.DataFrame, critical: pd.DataFrame, months: List[str]):
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Itens lidos", f"{len(df):,}".replace(',', '.'))
    col2.metric("Lojas identificadas", f"{df['empresa'].nunique():,}".replace(',', '.'))
    col3.metric("Itens críticos", f"{len(critical):,}".replace(',', '.'))
    col4.metric("Sem giro no grupo", f"{(critical['acao'] == ACTION_SALES).sum():,}".replace(',', '.'))

    show_cols = [
        "acao", "empresa_origem", "linha", "codigo_item", "descricao",
        *months[:4], "media", "estoque", "dt_ult_comp", "empresa_destino", "recomendacao"
    ]
    show_cols = [c for c in show_cols if c in critical.columns]
    st.dataframe(format_table(critical[show_cols], months), use_container_width=True, hide_index=True)



def page_by_action(critical: pd.DataFrame, months: List[str], action_name: str):
    subset = critical[critical["acao"] == action_name].copy()
    if subset.empty:
        st.info("Nenhum item encontrado para esse filtro.")
        return

    linhas = sorted([x for x in subset["linha"].dropna().unique().tolist() if str(x).strip()])
    linha_sel = st.selectbox("Filtrar por Linha", ["Todas"] + linhas, key=f"linha_{action_name}")
    if linha_sel != "Todas":
        subset = subset[subset["linha"] == linha_sel]

    lojas = sorted(subset["empresa_origem"].dropna().unique().tolist())
    loja_sel = st.selectbox("Filtrar por Loja", ["Todas"] + lojas, key=f"loja_{action_name}")
    if loja_sel != "Todas":
        subset = subset[subset["empresa_origem"] == loja_sel]

    for linha in sorted(subset["linha"].fillna("Sem linha").unique().tolist()):
        bloco = subset[subset["linha"].fillna("Sem linha") == linha].copy()
        with st.expander(f"Linha: {linha} ({len(bloco)} itens)", expanded=False):
            cols = [
                "empresa_origem", "codigo_item", "descricao", *months[:4], "media", "estoque",
                "dt_ult_comp", "empresa_destino", "motivo", "recomendacao"
            ]
            cols = [c for c in cols if c in bloco.columns]
            st.dataframe(format_table(bloco[cols], months), use_container_width=True, hide_index=True)



def page_by_store(critical: pd.DataFrame, months: List[str]):
    lojas = sorted(critical["empresa_origem"].dropna().unique().tolist())
    selected = st.selectbox("Selecione a loja", lojas)
    subset = critical[critical["empresa_origem"] == selected].copy()

    st.subheader("Produtos para ação de vendas")
    vendas = subset[subset["acao"] == ACTION_SALES].copy()
    if vendas.empty:
        st.info("Nenhum produto sem giro no grupo para essa loja.")
    else:
        for linha in sorted(vendas["linha"].fillna("Sem linha").unique().tolist()):
            bloco = vendas[vendas["linha"].fillna("Sem linha") == linha].copy()
            with st.expander(f"Linha: {linha} ({len(bloco)} itens)", expanded=False):
                cols = ["codigo_item", "descricao", *months[:4], "media", "estoque", "dt_ult_comp", "motivo", "recomendacao"]
                cols = [c for c in cols if c in bloco.columns]
                st.dataframe(format_table(bloco[cols], months), use_container_width=True, hide_index=True)

    st.subheader("Produtos com sugestão de transferência")
    transf = subset[subset["acao"] == ACTION_TRANSFER].copy()
    if transf.empty:
        st.info("Nenhum produto com sugestão de transferência para essa loja.")
    else:
        for linha in sorted(transf["linha"].fillna("Sem linha").unique().tolist()):
            bloco = transf[transf["linha"].fillna("Sem linha") == linha].copy()
            with st.expander(f"Linha: {linha} ({len(bloco)} itens)", expanded=False):
                cols = [
                    "codigo_item", "descricao", *months[:4], "media", "estoque",
                    "dt_ult_comp", "empresa_destino", "media_destino", "estoque_destino", "motivo", "recomendacao"
                ]
                cols = [c for c in cols if c in bloco.columns]
                st.dataframe(format_table(bloco[cols], months), use_container_width=True, hide_index=True)

    st.subheader("Produtos puxados recentemente")
    recentes = subset[subset["acao"] == ACTION_RECENT].copy()
    if recentes.empty:
        st.info("Nenhum produto puxado recentemente para essa loja.")
    else:
        for linha in sorted(recentes["linha"].fillna("Sem linha").unique().tolist()):
            bloco = recentes[recentes["linha"].fillna("Sem linha") == linha].copy()
            with st.expander(f"Linha: {linha} ({len(bloco)} itens)", expanded=False):
                cols = ["codigo_item", "descricao", *months[:4], "media", "estoque", "dt_ult_comp", "motivo", "recomendacao"]
                cols = [c for c in cols if c in bloco.columns]
                st.dataframe(format_table(bloco[cols], months), use_container_width=True, hide_index=True)


st.title("Análise de Produtos com Baixo Giro e Estoque Alto")
st.caption("Leitura otimizada do PDF com cache. O arquivo só é processado quando você clicar em Processar PDF.")

with st.sidebar:
    st.header("Parâmetros da análise")
    low_avg_limit = st.number_input("Média máxima para considerar baixo giro", min_value=0.0, value=1.0, step=0.1)
    high_stock_limit = st.number_input("Estoque mínimo para considerar estoque alto", min_value=0.0, value=3.0, step=1.0)
    min_absorption = st.number_input("Absorção mínima mensal exigida na loja destino", min_value=0.0, value=3.0, step=1.0)
    recent_purchase_days = st.number_input("Dias para bloquear ação após última compra", min_value=1, value=30, step=1)
    st.markdown("**Regras:** a loja destino só entra como opção se a média mensal do item for pelo menos o maior valor entre 30% do estoque da origem e a absorção mínima definida acima. Se a última compra tiver ocorrido dentro da janela definida, o item será apenas sinalizado como puxado recentemente, sem ação sugerida.")

uploaded_file = st.file_uploader("Envie o PDF do relatório de giro", type=["pdf"])

if uploaded_file is not None:
    st.info("Arquivo recebido. Clique em Processar PDF.")

if uploaded_file is not None and st.button("Processar PDF", type="primary"):
    raw = uploaded_file.getvalue()
    with st.spinner("Lendo e estruturando o PDF..."):
        pages_text, total_pages, engine = extract_text_pages_cached(raw)
        parsed = parse_pdf_bytes(raw)

    if parsed.errors:
        for err in parsed.errors:
            st.error(err)
    if parsed.df.empty:
        st.stop()

    critical = analyze_critical_items(parsed.df, parsed.months, low_avg_limit, high_stock_limit, min_absorption, int(recent_purchase_days))
    st.session_state["giro_df"] = parsed.df
    st.session_state["giro_critical"] = critical
    st.session_state["giro_months"] = parsed.months
    st.session_state["giro_engine"] = engine
    st.session_state["giro_pages"] = total_pages

if "giro_df" in st.session_state:
    df = st.session_state["giro_df"]
    critical = st.session_state["giro_critical"]
    months = st.session_state["giro_months"]

    info1, info2, info3 = st.columns(3)
    info1.success(f"PDF lido com {st.session_state.get('giro_engine', 'motor desconhecido')}")
    info2.info(f"Páginas: {st.session_state.get('giro_pages', 0)}")
    info3.info(f"Itens estruturados: {len(df):,}".replace(',', '.'))

    pagina = st.radio(
        "Navegação",
["Resumo geral", ACTION_SALES, ACTION_TRANSFER, ACTION_RECENT, "Por loja", "Rede do item"],
        horizontal=True,
    )

    if pagina == "Resumo geral":
        page_summary(df, critical, months)
    elif pagina == ACTION_SALES:
        page_by_action(critical, months, ACTION_SALES)
    elif pagina == ACTION_TRANSFER:
        page_by_action(critical, months, ACTION_TRANSFER)
    elif pagina == ACTION_RECENT:
        page_by_action(critical, months, ACTION_RECENT)
    elif pagina == "Por loja":
        page_by_store(critical, months)
    elif pagina == "Rede do item":
        if critical.empty:
            st.info("Nenhum item crítico encontrado.")
        else:
            item_options = critical["item"].drop_duplicates().sort_values().tolist()
            selected_item = st.selectbox("Selecione um item crítico", item_options)
            selected_code = selected_item.split(" - ")[0].strip()
            network = build_network_view(df, selected_code, months)
            if not network.empty:
                st.dataframe(format_table(network, months), use_container_width=True, hide_index=True)
                summary = critical[critical["codigo_item"] == selected_code].head(1)
                if not summary.empty:
                    st.warning(summary.iloc[0]["recomendacao"])

    with st.expander("Ver dados brutos extraídos do PDF"):
        st.dataframe(df, use_container_width=True, hide_index=True)

    csv = critical.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig") if not critical.empty else b""
    st.download_button(
        "Baixar análise em CSV",
        data=csv,
        file_name="analise_giro_criticos.csv",
        mime="text/csv",
        disabled=critical.empty,
    )

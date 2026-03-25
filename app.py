import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

st.set_page_config(page_title="Análise de Giro por PDF", layout="wide")

UNIT_TOKENS = {
    "UN", "KG", "PC", "PCT", "PT", "CX", "FD", "RL", "LT", "L", "ML",
    "GL", "BD", "SC", "PAR", "JG", "KIT", "CJ", "TB", "EMB"
}
NUM_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$|^-?\d+,\d{2}$|^-?\d+$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}(?:\d{2})?$|^\d{2}/\d{2}/\d{4}$")
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
    extractor: str = ""
    total_pages: int = 0


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


def split_description_and_unit(rest: str) -> Optional[Tuple[str, str, List[str]]]:
    tokens = rest.split()
    for i, token in enumerate(tokens):
        if token in UNIT_TOKENS:
            desc = " ".join(tokens[:i]).strip()
            right = tokens[i + 1:]
            if desc and right:
                return desc, token, right
    return None


def parse_months_from_pages(pages_text: List[str]) -> List[str]:
    sample_text = "\n".join(pages_text[:5])
    match = MONTH_HEADER_RE.search(sample_text)
    if not match:
        return ["Mês 1", "Mês 2", "Mês 3", "Mês 4"]
    months = [m.strip() for m in match.group(1).split(',') if m.strip()]
    months = list(reversed(months))
    return months[:4] if len(months) >= 4 else ["Mês 1", "Mês 2", "Mês 3", "Mês 4"]


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
    tail = [t.strip() for t in tail if t.strip()]

    # pdfplumber normalmente devolve 11 números sem data; alguns relatórios podem ter data antes do preço de venda
    if len(tail) < 11:
        return None

    date_idx = None
    for i, token in enumerate(tail):
        if DATE_RE.match(token):
            date_idx = i
            break

    if date_idx is not None:
        left = tail[:date_idx]
        right = tail[date_idx + 1:]
        if len(left) < 9 or len(right) < 2:
            return None
        month_vals = left[:4]
        media = left[4]
        previ30 = left[5]
        estoque = left[6]
        sugestao = left[7]
        pr_ult_comp = left[8]
        dt_ult_comp = tail[date_idx]
        pr_venda = right[0]
        lucro = right[1]
    else:
        month_vals = tail[:4]
        media = tail[4]
        previ30 = tail[5]
        estoque = tail[6]
        sugestao = tail[7]
        pr_ult_comp = tail[8]
        pr_venda = tail[9]
        lucro = tail[10]
        dt_ult_comp = ""

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
        "codigo_item": str(code),
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


def _extract_text_with_pdfplumber(raw: bytes) -> Tuple[List[str], int]:
    if pdfplumber is None:
        raise RuntimeError("pdfplumber não está disponível")
    texts = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return texts, total


def _extract_text_with_pypdf(raw: bytes) -> Tuple[List[str], int]:
    if PdfReader is None:
        raise RuntimeError("pypdf não está disponível")
    reader = PdfReader(io.BytesIO(raw))
    texts = []
    total = len(reader.pages)
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return texts, total


def _parse_rows_from_pages(pages_text: List[str], months: List[str]) -> List[dict]:
    rows: List[dict] = []
    company = ""
    line_name = ""
    group_name = ""

    ignore_prefixes = (
        "COD.", "UNICA ATACADISTA", "RELATORIO DO GIRO", "REFERENTE AOS MESES:", "PAGINA ",
        "FORNECEDOR:", "LINHA/GRUPO:", "UNIDADE:", "PRODUTO:", "MARCA:", "REFERENCIA:",
        "DESCRICAO:", "DIAS DE ABASTECIMENTO:", "EMPRESA:", "NIVEL DE GIRO:", "IMPRIME ITENS",
        "TABELA:", "PEDIDOS DE VENDA:", "PREÇO ÚLTIMA", "SALDOS:", "EMITIDO EM"
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
    return rows


@st.cache_data(show_spinner=False)
def parse_pdf_bytes(raw: bytes) -> ParseResult:
    attempts = []
    errors: List[str] = []

    if pdfplumber is not None:
        try:
            pages_text, total_pages = _extract_text_with_pdfplumber(raw)
            months = parse_months_from_pages(pages_text)
            rows = _parse_rows_from_pages(pages_text, months)
            attempts.append(("pdfplumber", pages_text, total_pages, months, rows))
        except Exception as e:
            errors.append(f"pdfplumber: {e}")

    if PdfReader is not None:
        try:
            pages_text, total_pages = _extract_text_with_pypdf(raw)
            months = parse_months_from_pages(pages_text)
            rows = _parse_rows_from_pages(pages_text, months)
            attempts.append(("PyPDF", pages_text, total_pages, months, rows))
        except Exception as e:
            errors.append(f"PyPDF: {e}")

    if not attempts:
        return ParseResult(pd.DataFrame(), [], ["Não foi possível ler o PDF."] + errors)

    best = max(attempts, key=lambda x: len(x[4]))
    extractor, pages_text, total_pages, months, rows = best

    if not rows:
        return ParseResult(
            pd.DataFrame(),
            months,
            ["Nenhum item foi estruturado. Verifique se o PDF segue o mesmo padrão do relatório de giro."] + errors,
            extractor=extractor,
            total_pages=total_pages,
        )

    df = pd.DataFrame(rows)
    df = df[df["codigo_item"].astype(str).str.len() >= 4].copy()
    df["item"] = df["codigo_item"].astype(str) + " - " + df["descricao"].astype(str)
    return ParseResult(df, months, errors, extractor=extractor, total_pages=total_pages)


def analyze_critical_items(
    df: pd.DataFrame,
    months: List[str],
    low_avg_limit: float,
    high_stock_limit: float,
    min_absorption: float,
    recent_days_block: int,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["dt_ult_comp_date"] = work["dt_ult_comp"].apply(parse_br_date)
    today = date.today()
    block_date = today - timedelta(days=int(recent_days_block))
    work["compra_recente"] = work["dt_ult_comp_date"].apply(lambda d: pd.notna(d) and d >= block_date)
    work["cobertura_meses"] = work.apply(lambda x: (x["estoque"] / x["media"]) if x["media"] > 0 else 9999, axis=1)
    work["item_critico"] = (work["media"] <= low_avg_limit) & (work["estoque"] >= high_stock_limit)
    critical = work[work["item_critico"]].copy()

    if critical.empty:
        return critical

    result_rows = []
    for _, row in critical.iterrows():
        months_dict = {m: row[m] for m in months[:4] if m in row.index}
        base = {
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
            "dt_ult_comp": row["dt_ult_comp"],
            "pr_ult_comp": row["pr_ult_comp"],
            "pr_venda": row["pr_venda"],
            "lucro_pct": row["lucro_pct"],
        }

        if row["compra_recente"]:
            result_rows.append({
                **base,
                "acao": ACTION_RECENT,
                "absorcao_minima_exigida": 0.0,
                "empresa_destino": "",
                "media_destino": 0.0,
                "estoque_destino": 0.0,
                "qtd_sugerida_transferencia": 0.0,
                "motivo": f"Última compra em {row['dt_ult_comp']}. Produto puxado para loja recentemente.",
                "recomendacao": "Produto puxado para loja recentemente.",
            })
            continue

        same_item = work[work["codigo_item"] == row["codigo_item"]].copy()
        other_stores = same_item[same_item["empresa"] != row["empresa"]].copy()
        absorcao_minima = max(min_absorption, row["estoque"] * 0.30)

        eligible = other_stores[other_stores["media"] >= absorcao_minima].copy()
        if not eligible.empty:
            eligible["score_destino"] = eligible["media"] - eligible["estoque"]
            eligible = eligible.sort_values(["score_destino", "media", "estoque"], ascending=[False, False, True])
            dest = eligible.iloc[0]
            qtd_sugerida = min(row["estoque"], max(absorcao_minima, dest["media"]))
            result_rows.append({
                **base,
                "acao": ACTION_TRANSFER,
                "absorcao_minima_exigida": absorcao_minima,
                "empresa_destino": dest["empresa"],
                "media_destino": dest["media"],
                "estoque_destino": dest["estoque"],
                "qtd_sugerida_transferencia": qtd_sugerida,
                "motivo": (
                    f"Baixo giro na origem: média {number_br(row['media'])} e estoque {number_br(row['estoque'])}. "
                    f"Destino sugerido {dest['empresa']} porque gira {number_br(dest['media'])}/mês e tem estoque atual {number_br(dest['estoque'])}."
                ),
                "recomendacao": (
                    f"Transferir para {dest['empresa']}, pois essa loja possui média {number_br(dest['media'])} ao mês, "
                    f"estoque atual {number_br(dest['estoque'])} e consegue absorver pelo menos {number_br(absorcao_minima)} por mês."
                ),
            })
        else:
            result_rows.append({
                **base,
                "acao": ACTION_SALES,
                "absorcao_minima_exigida": absorcao_minima,
                "empresa_destino": "",
                "media_destino": 0.0,
                "estoque_destino": 0.0,
                "qtd_sugerida_transferencia": 0.0,
                "motivo": (
                    "Baixo giro na origem e nenhuma loja do grupo apresentou giro suficiente para absorver ao menos "
                    f"{number_br(absorcao_minima)} por mês."
                ),
                "recomendacao": "Produto sem giro no grupo, fazer ação de vendas urgente!",
            })

    result = pd.DataFrame(result_rows)
    return result.sort_values(["empresa_origem", "linha", "acao", "estoque"], ascending=[True, True, True, False])


def build_network_view(df: pd.DataFrame, codigo_item: str, months: List[str]) -> pd.DataFrame:
    net = df[df["codigo_item"] == str(codigo_item)].copy()
    if net.empty:
        return net
    cols = ["empresa", "linha", "grupo", "codigo_item", "descricao"] + months[:4] + [
        "media", "previ30", "estoque", "sugestao", "dt_ult_comp", "pr_venda", "lucro_pct"
    ]
    available = [c for c in cols if c in net.columns]
    return net[available].sort_values(["media", "estoque"], ascending=[False, False])


def format_numeric_columns(df: pd.DataFrame, months: List[str]) -> pd.DataFrame:
    out = df.copy()
    num_cols = [
        c for c in months[:4] + [
            "media", "previ30", "estoque", "sugestao_relatorio", "cobertura_meses",
            "absorcao_minima_exigida", "media_destino", "estoque_destino", "qtd_sugerida_transferencia",
            "pr_ult_comp", "pr_venda", "lucro_pct"
        ] if c in out.columns
    ]
    for c in num_cols:
        out[c] = out[c].apply(number_br)
    return out


def render_grouped_by_line(df_view: pd.DataFrame, months: List[str], key_prefix: str) -> None:
    if df_view.empty:
        st.info("Nenhum item encontrado para os filtros aplicados.")
        return

    for linha in sorted(df_view["linha"].fillna("SEM LINHA").unique()):
        bloco = df_view[df_view["linha"].fillna("SEM LINHA") == linha].copy()
        with st.expander(f"{linha} ({len(bloco)} itens)", expanded=False):
            cols = [
                "empresa_origem", "codigo_item", "descricao", *months[:4], "media", "estoque",
                "dt_ult_comp", "empresa_destino", "qtd_sugerida_transferencia", "acao"
            ]
            cols = [c for c in cols if c in bloco.columns]
            st.dataframe(format_numeric_columns(bloco[cols], months), use_container_width=True, hide_index=True)

            opcoes = bloco["item"].drop_duplicates().sort_values().tolist()
            selected_item = st.selectbox(
                f"Ver item em todas as lojas - {linha}",
                opcoes,
                key=f"{key_prefix}_{linha}",
            )
            item_sel = bloco[bloco["item"] == selected_item].iloc[0]
            st.markdown(f"**Motivo:** {item_sel['motivo']}")
            st.markdown(f"**Recomendação:** {item_sel['recomendacao']}")
            selected_code = selected_item.split(" - ")[0].strip()
            rede = build_network_view(st.session_state["df_giro"], selected_code, months)
            st.dataframe(format_numeric_columns(rede, months), use_container_width=True, hide_index=True)


def render_store_page(store_df: pd.DataFrame, months: List[str], store_name: str) -> None:
    st.subheader(store_name)
    if store_df.empty:
        st.info("Nenhum item para esta loja com os filtros atuais.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Itens para ação", len(store_df))
    c2.metric("Sem giro no grupo", int((store_df["acao"] == ACTION_SALES).sum()))
    c3.metric("Sugestões de transferência", int((store_df["acao"] == ACTION_TRANSFER).sum()))

    tab1, tab2, tab3 = st.tabs(["Produtos para ação de vendas", "Produtos com sugestão de transferência", "Produtos puxados recentemente"])
    with tab1:
        render_grouped_by_line(store_df[store_df["acao"] == ACTION_SALES].copy(), months, f"sales_{store_name}")
    with tab2:
        render_grouped_by_line(store_df[store_df["acao"] == ACTION_TRANSFER].copy(), months, f"transfer_{store_name}")
    with tab3:
        render_grouped_by_line(store_df[store_df["acao"] == ACTION_RECENT].copy(), months, f"recent_{store_name}")


st.title("Análise de Giro por Loja e Ação Recomendada")
st.caption("Upload do PDF, leitura estruturada e separação entre produtos sem giro no grupo, transferência e itens puxados recentemente.")

with st.sidebar:
    st.header("Parâmetros da análise")
    low_avg_limit = st.number_input("Média máxima para considerar baixo giro", min_value=0.0, value=1.0, step=0.1)
    high_stock_limit = st.number_input("Estoque mínimo para considerar estoque alto", min_value=0.0, value=3.0, step=1.0)
    min_absorption = st.number_input("Absorção mínima mensal exigida na loja destino", min_value=0.0, value=3.0, step=1.0)
    recent_days_block = st.number_input("Dias para bloquear ação após última compra", min_value=0, value=30, step=1)

uploaded_file = st.file_uploader("Envie o PDF do relatório de giro", type=["pdf"], key="pdf_giro")

if "df_giro" not in st.session_state:
    st.session_state["df_giro"] = pd.DataFrame()
if "critical_giro" not in st.session_state:
    st.session_state["critical_giro"] = pd.DataFrame()
if "months_giro" not in st.session_state:
    st.session_state["months_giro"] = []
if "extractor_giro" not in st.session_state:
    st.session_state["extractor_giro"] = ""

if uploaded_file is not None:
    st.info("Arquivo recebido. Clique em Processar PDF para iniciar a análise.")

if uploaded_file is not None and st.button("Processar PDF", type="primary", key="processar_pdf"):
    try:
        raw = uploaded_file.read()
        with st.spinner("Lendo e estruturando o PDF..."):
            parsed = parse_pdf_bytes(raw)
        if parsed.errors:
            for err in parsed.errors:
                if "Nenhum item" in err or "Não foi possível" in err:
                    st.error(err)
                else:
                    st.warning(err)
        if parsed.df.empty:
            st.stop()
        critical = analyze_critical_items(
            parsed.df,
            parsed.months,
            low_avg_limit,
            high_stock_limit,
            min_absorption,
            recent_days_block,
        )
        st.session_state["df_giro"] = parsed.df
        st.session_state["critical_giro"] = critical
        st.session_state["months_giro"] = parsed.months
        st.session_state["extractor_giro"] = parsed.extractor
        st.success(f"PDF processado com sucesso usando {parsed.extractor}. Itens lidos: {len(parsed.df):,}".replace(",", "."))
    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        st.exception(e)

if not st.session_state["critical_giro"].empty:
    df = st.session_state["df_giro"]
    critical = st.session_state["critical_giro"]
    months = st.session_state["months_giro"]

    with st.sidebar:
        st.header("Filtros da visualização")
        action_options = [ACTION_SALES, ACTION_TRANSFER, ACTION_RECENT]
        selected_actions = st.multiselect(
            "Tipo de produto",
            options=action_options,
            default=action_options,
            key="filtro_tipo_produto",
        )
        linhas = sorted([x for x in critical["linha"].dropna().unique().tolist() if x])
        selected_lines = st.multiselect("Linhas", options=linhas, default=[], key="filtro_linhas")
        lojas = sorted(critical["empresa_origem"].dropna().unique().tolist())
        selected_store_page = st.selectbox("Página por loja", options=lojas, key="pagina_loja") if lojas else None
        page = st.radio(
            "Navegação",
            options=["Resumo geral", ACTION_SALES, ACTION_TRANSFER, ACTION_RECENT, "Página por loja"],
            index=0,
            key="nav_page",
        )

    view = critical.copy()
    if selected_actions:
        view = view[view["acao"].isin(selected_actions)].copy()
    if selected_lines:
        view = view[view["linha"].isin(selected_lines)].copy()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Itens lidos", f"{len(df):,}".replace(',', '.'))
    col2.metric("Lojas identificadas", f"{df['empresa'].nunique():,}".replace(',', '.'))
    col3.metric("Sem giro no grupo", f"{(critical['acao'] == ACTION_SALES).sum():,}".replace(',', '.'))
    col4.metric("Sugestões de transferência", f"{(critical['acao'] == ACTION_TRANSFER).sum():,}".replace(',', '.'))
    col5.metric("Puxados recentemente", f"{(critical['acao'] == ACTION_RECENT).sum():,}".replace(',', '.'))
    st.caption(f"Leitura selecionada automaticamente: {st.session_state['extractor_giro']}")

    with st.expander("Ver dados brutos extraídos do PDF"):
        st.dataframe(df, use_container_width=True, hide_index=True)

    if page == "Resumo geral":
        st.subheader("Resumo geral")
        resumo_cols = [
            "empresa_origem", "linha", "grupo", "codigo_item", "descricao", *months[:4], "media",
            "estoque", "dt_ult_comp", "acao", "empresa_destino", "qtd_sugerida_transferencia"
        ]
        resumo_cols = [c for c in resumo_cols if c in view.columns]
        st.dataframe(format_numeric_columns(view[resumo_cols], months), use_container_width=True, hide_index=True)
        csv = view.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("Baixar análise filtrada em CSV", data=csv, file_name="analise_giro_filtrada.csv", mime="text/csv")
        st.markdown("### Drill por Linha")
        render_grouped_by_line(view, months, "geral")
    elif page == ACTION_SALES:
        st.subheader("Produtos sem giro no grupo")
        render_grouped_by_line(view[view["acao"] == ACTION_SALES].copy(), months, "sales_page")
    elif page == ACTION_TRANSFER:
        st.subheader("Produtos com sugestão de transferência")
        render_grouped_by_line(view[view["acao"] == ACTION_TRANSFER].copy(), months, "transfer_page")
    elif page == ACTION_RECENT:
        st.subheader("Produtos puxados recentemente")
        render_grouped_by_line(view[view["acao"] == ACTION_RECENT].copy(), months, "recent_page")
    elif page == "Página por loja" and selected_store_page:
        render_store_page(view[view["empresa_origem"] == selected_store_page].copy(), months, selected_store_page)
else:
    st.warning("Depois de processar o PDF, as páginas de análise e os filtros laterais serão habilitados.")

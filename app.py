import io
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="Análise de Giro por PDF", layout="wide")

UNIT_TOKENS = {
    "UN", "KG", "PC", "PCT", "PT", "CX", "FD", "RL", "LT", "L", "ML",
    "GL", "BD", "SC", "PAR", "JG", "KIT", "CJ", "TB", "EMB"
}
NUM_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}$|^-?\d+,\d{2}$|^-?\d+$")
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
CODE_RE = re.compile(r"^(\d{4,6})\s+(.*)$")
MONTH_HEADER_RE = re.compile(r"REFERENTE AOS MESES:\s*([^\n]+)")


@dataclass
class ParseResult:
    df: pd.DataFrame
    months: List[str]
    errors: List[str]



def br_to_float(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    value = value.replace('.', '').replace(',', '.')
    try:
        return float(value)
    except Exception:
        return 0.0



def money_br(value: float) -> str:
    return f"R$ {value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')



def number_br(value: float) -> str:
    return f"{value:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')



def extract_text_pages(uploaded_file) -> Tuple[List[str], int]:
    raw = uploaded_file.read()
    uploaded_file.seek(0)
    pages_text = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total_pages = len(pdf.pages)
        progress = st.progress(0, text="Lendo páginas do PDF...")
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages_text.append(text)
            progress.progress((i + 1) / total_pages, text=f"Lendo páginas do PDF... {i + 1}/{total_pages}")
        progress.empty()
    return pages_text, total_pages



def parse_months_from_text(all_text: str) -> List[str]:
    match = MONTH_HEADER_RE.search(all_text)
    if not match:
        return ["Mês 1", "Mês 2", "Mês 3", "Mês 4"]
    months = [m.strip() for m in match.group(1).split(',') if m.strip()]
    months = list(reversed(months))
    return months[:4] if len(months) >= 4 else months



def split_description_and_unit(rest: str) -> Optional[Tuple[str, str, List[str]]]:
    tokens = rest.split()
    for i, token in enumerate(tokens):
        if token in UNIT_TOKENS:
            desc = " ".join(tokens[:i]).strip()
            right = tokens[i + 1 :]
            if desc and right:
                return desc, token, right
    return None



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

    # Campos esperados após a unidade:
    # 4 meses + media + previ30 + estoque + sugestao + pr_ult_comp + [dt_ult_comp] + pr_venda + lucro
    date_idx = None
    for i, token in enumerate(tail):
        if DATE_RE.match(token):
            date_idx = i
            break

    # linha normal com data
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
        # fallback sem data
        if len(tail) < 11:
            return None
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
    row = {
        "empresa": company,
        "codigo_loja": company.split(" - ")[0].strip() if " - " in company else company.strip(),
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
    return row



def parse_pdf(uploaded_file) -> ParseResult:
    pages_text, total_pages = extract_text_pages(uploaded_file)
    all_text = "\n".join(pages_text)
    months = parse_months_from_text(all_text)
    errors = []
    rows = []

    company = ""
    line_name = ""
    group_name = ""

    parse_bar = st.progress(0, text="Estruturando dados do relatório...")
    for page_idx, text in enumerate(pages_text):
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
            if line.startswith(("COD.", "UNICA ATACADISTA", "RELATORIO DO GIRO", "REFERENTE AOS MESES:", "PAGINA ", "FORNECEDOR:", "LINHA/GRUPO:", "UNIDADE:", "PRODUTO:", "MARCA:", "REFERENCIA:", "DESCRICAO:", "DIAS DE ABASTECIMENTO:", "EMPRESA:", "NIVEL DE GIRO:", "IMPRIME ITENS", "TABELA:", "PEDIDOS DE VENDA:", "PREÇO ÚLTIMA", "SALDOS:")):
                continue
            row = parse_product_line(line, company, line_name, group_name, months)
            if row:
                rows.append(row)
        parse_bar.progress((page_idx + 1) / total_pages, text=f"Estruturando dados do relatório... {page_idx + 1}/{total_pages}")
    parse_bar.empty()

    if not rows:
        errors.append("Nenhum item foi estruturado. Verifique se o PDF segue o mesmo padrão do relatório de giro.")
        return ParseResult(pd.DataFrame(), months, errors)

    df = pd.DataFrame(rows)
    df = df[df["codigo_item"].astype(str).str.len() >= 4].copy()
    df["item"] = df["codigo_item"].astype(str) + " - " + df["descricao"].astype(str)
    return ParseResult(df, months, errors)



def analyze_critical_items(df: pd.DataFrame, months: List[str], low_avg_limit: float, high_stock_limit: float, min_absorption: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    work = df.copy()
    work["cobertura_meses"] = work.apply(lambda x: (x["estoque"] / x["media"]) if x["media"] > 0 else 9999, axis=1)
    work["excesso_vs_media"] = (work["estoque"] - work["media"]).clip(lower=0)
    work["item_critico"] = (work["media"] <= low_avg_limit) & (work["estoque"] >= high_stock_limit)
    critical = work[work["item_critico"]].copy()

    if critical.empty:
        return critical

    result_rows = []
    for _, row in critical.iterrows():
        same_item = work[work["codigo_item"] == row["codigo_item"]].copy()
        other_stores = same_item[same_item["empresa"] != row["empresa"]].copy()

        required_qty = max(min_absorption, row["estoque"] * 0.30)
        eligible = other_stores[other_stores["media"] >= required_qty].copy()
        eligible = eligible.sort_values(["media", "estoque"], ascending=[False, True])

        if not eligible.empty:
            dest = eligible.iloc[0]
            action = "Transferir"
            recommendation = (
                f"Transferir prioritariamente para {dest['empresa']}, média {number_br(dest['media'])}/mês "
                f"e estoque {number_br(dest['estoque'])}."
            )
            destino = dest["empresa"]
            destino_media = dest["media"]
            destino_estoque = dest["estoque"]
            destino_previ30 = dest["previ30"]
        else:
            action = "Ação de vendas urgente"
            recommendation = "Produto sem giro no grupo, fazer ação de vendas urgente!"
            destino = ""
            destino_media = 0.0
            destino_estoque = 0.0
            destino_previ30 = 0.0

        months_dict = {m: row[m] for m in months[:4] if m in row.index}
        result_rows.append(
            {
                "status": "Crítico",
                "acao": action,
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
                "pr_venda": row["pr_venda"],
                "lucro_pct": row["lucro_pct"],
            }
        )

    result = pd.DataFrame(result_rows)
    result = result.sort_values(["acao", "estoque", "media"], ascending=[True, False, True])
    return result



def build_network_view(df: pd.DataFrame, codigo_item: str, months: List[str]) -> pd.DataFrame:
    net = df[df["codigo_item"] == str(codigo_item)].copy()
    if net.empty:
        return net
    cols = ["empresa", "linha", "grupo", "codigo_item", "descricao"] + months[:4] + ["media", "previ30", "estoque", "sugestao", "pr_venda", "lucro_pct"]
    available = [c for c in cols if c in net.columns]
    net = net[available].sort_values(["media", "estoque"], ascending=[False, False])
    return net



def format_table(df: pd.DataFrame, months: List[str]) -> pd.DataFrame:
    out = df.copy()
    num_cols = [c for c in months[:4] + ["media", "previ30", "estoque", "sugestao_relatorio", "cobertura_meses", "absorcao_minima_exigida", "media_destino", "estoque_destino", "previ30_destino", "pr_venda", "lucro_pct"] if c in out.columns]
    for c in num_cols:
        out[c] = out[c].apply(number_br)
    return out


st.title("Análise de Produtos com Baixo Giro e Estoque Alto")
st.caption("Suba o PDF do relatório de giro, processe o arquivo e identifique itens críticos com sugestão de transferência entre lojas.")

with st.sidebar:
    st.header("Parâmetros da análise")
    low_avg_limit = st.number_input("Média máxima para considerar baixo giro", min_value=0.0, value=1.0, step=0.1)
    high_stock_limit = st.number_input("Estoque mínimo para considerar estoque alto", min_value=0.0, value=3.0, step=1.0)
    min_absorption = st.number_input("Absorção mínima mensal exigida na loja destino", min_value=0.0, value=3.0, step=1.0)
    st.markdown("**Regra de destino:** a loja só entra como opção se a média mensal do item for pelo menos o maior valor entre 30% do estoque da origem e a absorção mínima definida acima.")

uploaded_file = st.file_uploader("Envie o PDF do relatório de giro", type=["pdf"])

if uploaded_file is not None:
    st.info("Arquivo recebido. Clique em **Processar PDF** para iniciar a leitura.")

if uploaded_file is not None and st.button("Processar PDF", type="primary"):
    try:
        parsed = parse_pdf(uploaded_file)
        if parsed.errors:
            for err in parsed.errors:
                st.error(err)
        if parsed.df.empty:
            st.stop()

        df = parsed.df
        months = parsed.months
        critical = analyze_critical_items(df, months, low_avg_limit, high_stock_limit, min_absorption)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Itens lidos", f"{len(df):,}".replace(',', '.'))
        col2.metric("Lojas identificadas", f"{df['empresa'].nunique():,}".replace(',', '.'))
        col3.metric("Itens críticos", f"{len(critical):,}".replace(',', '.'))
        col4.metric("Itens sem destino", f"{(critical['acao'] == 'Ação de vendas urgente').sum():,}".replace(',', '.'))

        with st.expander("Ver dados brutos extraídos do PDF"):
            st.dataframe(df, use_container_width=True, hide_index=True)

        st.subheader("Tabela de itens críticos")
        if critical.empty:
            st.success("Nenhum item crítico foi encontrado com os parâmetros atuais.")
        else:
            show_cols = [
                "status", "acao", "empresa_origem", "codigo_item", "descricao",
                *months[:4], "media", "estoque", "cobertura_meses",
                "absorcao_minima_exigida", "empresa_destino", "media_destino", "recomendacao"
            ]
            show_cols = [c for c in show_cols if c in critical.columns]
            st.dataframe(format_table(critical[show_cols], months), use_container_width=True, hide_index=True)

            csv = critical.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button("Baixar análise em CSV", data=csv, file_name="analise_giro_criticos.csv", mime="text/csv")

            st.subheader("Visualização do mesmo item em outras lojas")
            item_options = critical["item"].drop_duplicates().sort_values().tolist()
            selected_item = st.selectbox("Selecione um item crítico", item_options)
            selected_code = selected_item.split(" - ")[0].strip()
            network = build_network_view(df, selected_code, months)
            if not network.empty:
                st.dataframe(network, use_container_width=True, hide_index=True)
                summary = critical[critical["codigo_item"] == selected_code].head(1)
                if not summary.empty:
                    rec = summary.iloc[0]["recomendacao"]
                    st.warning(rec)
            else:
                st.info("Nenhuma visão de rede encontrada para esse item.")

    except Exception as e:
        st.error(f"Erro ao processar o PDF: {e}")
        st.exception(e)

import Vue from "vue";
import CodeBlock from "../vue_components/code_block"
import Collapsible from "../vue_components/collapsible"

Vue.config.devtools = process.env.NODE_ENV != 'production';

const peptide_view_settings = {
    el: "#peptide-view",
    delimiters: ['[[',']]'],
    components: {
        CodeBlock,
        'codeblock': CodeBlock,
        'collapsible': Collapsible
    },
    data: {
        show_unreviewd_proteins: false
    },
    methods: {
        toggleUnreviewedProteins(){
            this.show_unreviewd_proteins = !this.show_unreviewd_proteins
        }
    }
}


document.addEventListener("DOMContentLoaded", () => {
    var peptide_view = document.getElementById("peptide-view");
    var peptide_view_app = null;
    if (peptide_view)
        peptide_view_app = new Vue(peptide_view_settings);
});
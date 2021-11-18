export default {
    delimiters: ['[[',']]'],
    props: {
        is_collapsed: {
            required: true,
            type: Boolean
        }
    },
    template: `
        <div class="collapse" :class="{show: is_collapsed}">
            <slot></slot>
        </div>
    `
}
# coaches/: estilos de entrenador

Cada `.md` de esta carpeta es una forma de entrenar. El activo se elige con:

```
./coach style            # lista los estilos (▶ = activo)
./coach style set grinder --why "quiero mejorar finales"   # cambia el estilo (queda en estado/decisiones.md)
```

o en `config.json` → `"coach_style": "grinder"`. El hook de inicio le avisa al coach cuál usar.

## Crear el tuyo
Copia `profe.md`, cámbiale el nombre (ej. `mi-coach.md`) y edita las secciones: **Voz**, **Filosofía**, **Revisando partidas**, **Entrenando** y **Nunca**.

**Regla del grupo:** los estilos pueden *inspirarse* en cómo juega o enseña alguien famoso (sus aperturas, su enfoque, lo que prioriza). El coach **no se hace pasar** por esa persona ni le inventa frases o anécdotas. Habla como "tu coach", no como "Magnus".

El estilo cambia el *cómo*: tono, prioridades y tipo de ejercicios. Las reglas duras de `CLAUDE.md` (no inventar datos, usar Stockfish, máximo 3 lecciones) aplican siempre.

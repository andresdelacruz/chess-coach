---
name: diagnostico
description: Diagnóstico del jugador con todas sus partidas (o un período). Encuentra las 3 debilidades que más puntos le cuestan y arma un plan de 2 semanas con una métrica para re-medir. Úsala en la primera sesión, cada semana o con "en qué estoy fallando", "plan de entrenamiento", "cómo mejoro".
---

# Diagnóstico + plan

**Activadores:** primera sesión (perfil vacío), "en qué fallo", "plan", "cómo mejoro", fecha de re-medición del plan vigente.

**Entradas:** `./coach stats` (con cortes), `./coach repertoire`, `estado/objetivos.md`, `estado/plan.md` (si existe, para comparar) y `estado/decisiones.md`.

## Pasos
1. **Cobertura.** Si hay menos de 20 partidas analizadas: `./coach analyze --limit 40` (avisa: ~10 s por partida). Si `./coach doctor` marca anomalías, menciónalas antes de concluir.
2. **Cortes útiles**, solo los necesarios: `--time-class blitz` vs `rapid`, `--color white` / `black`, `--source` si tiene las dos plataformas, `--days 90`.
3. **Ranking por impacto en puntos:** errores por fase, % de errores en apuro de reloj, conversión de ventajas de 85% o más, cómo pierde, aperturas con score bajo (mínimo 5 partidas) y cumplimiento del repertorio.
4. Elige **3 debilidades**, cada una con su número y 1–2 partidas de ejemplo (uuid o URL).
5. Si existía un plan, **compara la métrica base con la actual** antes de proponer el siguiente.
6. Si faltan objetivos (rating meta, ritmo, horas por semana), pregúntalos. No los inventes.

## Salida
- Al alumno: las 3 debilidades en 3 líneas y la propuesta de plan. **El plan es decisión del alumno**: espera su ok antes de darlo por vigente.
- `entregables/AAAA-MM-DD-diagnostico.md` con la evidencia completa.
- `estado/plan.md` (una vez aprobado): 1 hábito de partida, ejercicios de sus posiciones (`./coach positions …`), 1 tema de `knowledge/`, 1 sesión jugada a jugada de su salida más débil, **métrica base y fecha de re-medición**.
- `estado/perfil.md` (fortalezas y debilidades con evidencia; sin copiar ratings), `decisiones.md` ("plan aprobado") y la bitácora.

## Criterio de cierre
Cada debilidad tiene número y ejemplo; el plan tiene métrica y fecha; el alumno lo aprobó o quedó como pendiente en `estado/actual.md`.

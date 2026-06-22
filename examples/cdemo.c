#include "cardinal_rt.h"

typedef struct {
    int32_t x;
    int32_t y;
} cl_struct_Vec;

int32_t cl_cdemo__fact(int32_t v_n);
int32_t cl_cdemo__main(void);
int32_t cl_math__abs(int32_t v_x);
int32_t cl_math__min(int32_t v_a, int32_t v_b);
int32_t cl_math__max(int32_t v_a, int32_t v_b);
int32_t cl_math__clamp(int32_t v_x, int32_t v_lo, int32_t v_hi);
int32_t cl_math__pow(int32_t v_base, int32_t v_exp);
int32_t cl_array__sum(cl_array v_xs);
bool cl_array__contains(cl_array v_xs, int32_t v_target);
int32_t cl_array__max(cl_array v_xs);
void cl_array__fill(cl_array v_xs, int32_t v_value);

int32_t cl_cdemo__fact(int32_t v_n) {
    bool t0;
    int32_t t1;
    int32_t t2;
    int32_t t3;
    t0 = (v_n <= ((int32_t)1));
    if (t0) goto then2; else goto else3;
  then2:;
    return ((int32_t)1);
  else3:;
    goto if_end1;
  if_end1:;
    t1 = (int32_t)((v_n) - (((int32_t)1)));
    t2 = cl_cdemo__fact(t1);
    t3 = (int32_t)((v_n) * (t2));
    return t3;
}

int32_t cl_cdemo__main(void) {
    int32_t t0;
    int32_t t1;
    cl_struct_Vec t2;
    int32_t t3;
    int32_t t4;
    int32_t t5;
    int32_t t6;
    int32_t t7;
    int32_t t8;
    int32_t t9;
    cl_array t10;
    int32_t t11;
    bool t12;
    int32_t t13;
    int32_t t14;
    bool t15;
    int32_t t16;
    int32_t t17;
    int32_t t18;
    bool t19;
    uint32_t t20;
    uint8_t t21;
    int32_t t22;
    cl_struct_Vec v_v_1;
    cl_array v_xs_2;
    int32_t v_acc_3;
    int32_t v_i_4;
    int32_t v_k_5;
    int32_t v_d_6;
    t0 = cl_cdemo__fact(((int32_t)6));
    cl_print_i64(t0);
    cl_print_nl();
    t1 = cl_math__clamp(((int32_t)50), ((int32_t)0), ((int32_t)10));
    cl_print_i64(t1);
    cl_print_nl();
    t2 = (cl_struct_Vec){.x=((int32_t)3), .y=((int32_t)4)};
    v_v_1 = t2;
    t3 = (v_v_1).x;
    t4 = (v_v_1).x;
    t5 = (int32_t)((t3) * (t4));
    t6 = (v_v_1).y;
    t7 = (v_v_1).y;
    t8 = (int32_t)((t6) * (t7));
    t9 = (int32_t)((t5) + (t8));
    cl_print_i64(t9);
    cl_print_nl();
    t10 = cl_array_new(sizeof(int32_t), 4);
    *(int32_t*)cl_array_at(t10, 0) = ((int32_t)5);
    *(int32_t*)cl_array_at(t10, 1) = ((int32_t)10);
    *(int32_t*)cl_array_at(t10, 2) = ((int32_t)15);
    *(int32_t*)cl_array_at(t10, 3) = ((int32_t)20);
    v_xs_2 = t10;
    t11 = cl_array__sum(v_xs_2);
    cl_print_i64(t11);
    cl_print_nl();
    v_acc_3 = ((int32_t)0);
    v_i_4 = ((int32_t)0);
    goto for_head1;
  for_head1:;
    t12 = (v_i_4 < ((int32_t)5));
    if (t12) goto for_body2; else goto for_end4;
  for_body2:;
    t13 = (int32_t)((v_acc_3) + (v_i_4));
    v_acc_3 = t13;
    goto for_post3;
  for_post3:;
    t14 = (int32_t)((v_i_4) + (((int32_t)1)));
    v_i_4 = t14;
    goto for_head1;
  for_end4:;
    cl_print_i64(v_acc_3);
    cl_print_nl();
    v_k_5 = ((int32_t)0);
    goto while_head5;
  while_head5:;
    t15 = (v_k_5 < ((int32_t)3));
    if (t15) goto while_body6; else goto while_end7;
  while_body6:;
    t16 = (int32_t)((v_k_5) + (((int32_t)1)));
    v_k_5 = t16;
    goto while_head5;
  while_end7:;
    cl_print_i64(v_k_5);
    cl_print_nl();
    t17 = 1; /* Dir::East */
    v_d_6 = t17;
    t18 = 1; /* Dir::East */
    t19 = (v_d_6 == t18);
    if (t19) goto then9; else goto else10;
  then9:;
    cl_print_str(((cl_str){"east", 4}));
    cl_print_nl();
    goto if_end8;
  else10:;
    goto if_end8;
  if_end8:;
    t20 = (uint32_t)((((uint32_t)1u)) << (((uint32_t)5u)));
    cl_print_u64(t20);
    cl_print_nl();
    t21 = (uint8_t)(((int32_t)300));
    cl_print_u64(t21);
    cl_print_nl();
    t22 = (int32_t)((((int32_t)2000000000)) * (((int32_t)2)));
    cl_print_i64(t22);
    cl_print_nl();
    return ((int32_t)0);
}

int32_t cl_math__abs(int32_t v_x) {
    bool t0;
    int32_t t1;
    t0 = (v_x < ((int32_t)0));
    if (t0) goto then2; else goto else3;
  then2:;
    t1 = (int32_t)((((int32_t)0)) - (v_x));
    return t1;
  else3:;
    goto if_end1;
  if_end1:;
    return v_x;
}

int32_t cl_math__min(int32_t v_a, int32_t v_b) {
    bool t0;
    t0 = (v_a < v_b);
    if (t0) goto then2; else goto else3;
  then2:;
    return v_a;
  else3:;
    goto if_end1;
  if_end1:;
    return v_b;
}

int32_t cl_math__max(int32_t v_a, int32_t v_b) {
    bool t0;
    t0 = (v_a > v_b);
    if (t0) goto then2; else goto else3;
  then2:;
    return v_a;
  else3:;
    goto if_end1;
  if_end1:;
    return v_b;
}

int32_t cl_math__clamp(int32_t v_x, int32_t v_lo, int32_t v_hi) {
    bool t0;
    bool t1;
    t0 = (v_x < v_lo);
    if (t0) goto then2; else goto else3;
  then2:;
    return v_lo;
  else3:;
    goto if_end1;
  if_end1:;
    t1 = (v_x > v_hi);
    if (t1) goto then5; else goto else6;
  then5:;
    return v_hi;
  else6:;
    goto if_end4;
  if_end4:;
    return v_x;
}

int32_t cl_math__pow(int32_t v_base, int32_t v_exp) {
    bool t0;
    int32_t t1;
    int32_t t2;
    int32_t v_result_1;
    int32_t v_i_2;
    v_result_1 = ((int32_t)1);
    v_i_2 = ((int32_t)0);
    goto while_head1;
  while_head1:;
    t0 = (v_i_2 < v_exp);
    if (t0) goto while_body2; else goto while_end3;
  while_body2:;
    t1 = (int32_t)((v_result_1) * (v_base));
    v_result_1 = t1;
    t2 = (int32_t)((v_i_2) + (((int32_t)1)));
    v_i_2 = t2;
    goto while_head1;
  while_end3:;
    return v_result_1;
}

int32_t cl_array__sum(cl_array v_xs) {
    uint64_t t0;
    bool t1;
    int32_t t2;
    int32_t t3;
    uint64_t t4;
    int32_t v_total_1;
    uint64_t v_i_2;
    int32_t v_x_3;
    v_total_1 = ((int32_t)0);
    v_i_2 = ((uint64_t)0ull);
    t0 = (v_xs).len;
    goto fi_head1;
  fi_head1:;
    t1 = (v_i_2 < t0);
    if (t1) goto fi_body2; else goto fi_end4;
  fi_body2:;
    t2 = *(int32_t*)cl_array_at(v_xs, v_i_2);
    v_x_3 = t2;
    t3 = (int32_t)((v_total_1) + (v_x_3));
    v_total_1 = t3;
    goto fi_post3;
  fi_post3:;
    t4 = (uint64_t)((v_i_2) + (((uint64_t)1ull)));
    v_i_2 = t4;
    goto fi_head1;
  fi_end4:;
    return v_total_1;
}

bool cl_array__contains(cl_array v_xs, int32_t v_target) {
    uint64_t t0;
    bool t1;
    int32_t t2;
    bool t3;
    uint64_t t4;
    uint64_t v_i_1;
    int32_t v_x_2;
    v_i_1 = ((uint64_t)0ull);
    t0 = (v_xs).len;
    goto fi_head1;
  fi_head1:;
    t1 = (v_i_1 < t0);
    if (t1) goto fi_body2; else goto fi_end4;
  fi_body2:;
    t2 = *(int32_t*)cl_array_at(v_xs, v_i_1);
    v_x_2 = t2;
    t3 = (v_x_2 == v_target);
    if (t3) goto then6; else goto else7;
  then6:;
    return true;
  else7:;
    goto if_end5;
  if_end5:;
    goto fi_post3;
  fi_post3:;
    t4 = (uint64_t)((v_i_1) + (((uint64_t)1ull)));
    v_i_1 = t4;
    goto fi_head1;
  fi_end4:;
    return false;
}

int32_t cl_array__max(cl_array v_xs) {
    uint64_t t0;
    bool t1;
    int32_t t2;
    bool t3;
    int32_t t4;
    bool t5;
    int32_t t6;
    uint64_t t7;
    uint64_t v_n_1;
    int32_t v_best_2;
    uint64_t v_i_3;
    t0 = (v_xs).len;
    v_n_1 = t0;
    t1 = (v_n_1 == ((uint64_t)0ull));
    if (t1) goto then2; else goto else3;
  then2:;
    cl_panic(((cl_str){"array::max of empty array", 25}));
    goto if_end1;
  else3:;
    goto if_end1;
  if_end1:;
    t2 = *(int32_t*)cl_array_at(v_xs, ((uint64_t)0ull));
    v_best_2 = t2;
    v_i_3 = ((uint64_t)1ull);
    goto while_head4;
  while_head4:;
    t3 = (v_i_3 < v_n_1);
    if (t3) goto while_body5; else goto while_end6;
  while_body5:;
    t4 = *(int32_t*)cl_array_at(v_xs, v_i_3);
    t5 = (t4 > v_best_2);
    if (t5) goto then8; else goto else9;
  then8:;
    t6 = *(int32_t*)cl_array_at(v_xs, v_i_3);
    v_best_2 = t6;
    goto if_end7;
  else9:;
    goto if_end7;
  if_end7:;
    t7 = (uint64_t)((v_i_3) + (((uint64_t)1ull)));
    v_i_3 = t7;
    goto while_head4;
  while_end6:;
    return v_best_2;
}

void cl_array__fill(cl_array v_xs, int32_t v_value) {
    uint64_t t0;
    bool t1;
    uint64_t t2;
    uint64_t v_n_1;
    uint64_t v_i_2;
    t0 = (v_xs).len;
    v_n_1 = t0;
    v_i_2 = ((uint64_t)0ull);
    goto while_head1;
  while_head1:;
    t1 = (v_i_2 < v_n_1);
    if (t1) goto while_body2; else goto while_end3;
  while_body2:;
    *(int32_t*)cl_array_at(v_xs, v_i_2) = v_value;
    t2 = (uint64_t)((v_i_2) + (((uint64_t)1ull)));
    v_i_2 = t2;
    goto while_head1;
  while_end3:;
    return;
}

int main(void) { return (int)cl_cdemo__main(); }
